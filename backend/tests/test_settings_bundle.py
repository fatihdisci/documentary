"""Moving one installation's settings and credentials to another computer.

The behaviour these tests exist to pin down, in order of how much it would cost
to get wrong:

1. **The exported bytes contain no plaintext secret.** This is the whole reason
   the export is encrypted, and the test that would catch a regression to a
   plain JSON dump.
2. A wrong passphrase, or an edited file, is refused — and nothing on the
   receiving machine changes.
3. A round trip really restores the keys and the OAuth grants, at 0600.
4. Machine-specific paths are not carried over unless asked for, because a
   folder from the other computer usually does not exist here.

Every credential in this file is an obviously fake string.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from app.config import MutableSettings, Settings, get_settings
from app.config_bundle import (
    FILE_SUFFIX,
    BundleExportRequest,
    export_bundle,
    import_bundle,
    read_bundle_header,
)
from app.errors import ValidationError

PASSPHRASE = "iki-bilgisayar-arasi"

#: Distinctive enough that finding one in a byte stream is unambiguous.
FAKE_SECRETS = {
    "elevenlabs_api_key": "FAKE-ELEVENLABS-KEY-aaaaaaaaaaaa",
    "meta_app_secret": "FAKE-META-SECRET-bbbbbbbbbbbb",
    "meta_app_id": "1234567890123456",
    "tiktok_client_secret": "FAKE-TIKTOK-SECRET-cccccccccccc",
    "object_storage_secret_access_key": "FAKE-R2-SECRET-dddddddddddd",
}

FAKE_TOKEN_FILE = json.dumps(
    {"token": "FAKE-TOKEN-eeeeeeeeeeee", "refresh_token": "FAKE-REFRESH-ffffffffffff"}
)


def seed_installation(settings: Settings) -> None:
    """A machine that has been set up: preferences, keys and OAuth grants."""
    settings.save_mutable(
        settings.mutable.model_copy(
            update={
                "default_voice": "af_heart",
                "default_fps": 30,
                "media_host_provider": "s3",
                "object_storage_bucket": "evb-temp",
                "projects_dir": "/Volumes/PC1/projects",
            }
        )
    )
    for name, value in FAKE_SECRETS.items():
        settings.set_secret(name, value)

    directory = settings.oauth_secrets_dir
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "youtube-upload-token.json").write_text(FAKE_TOKEN_FILE, "utf-8")
    (directory / "meta-token.json").write_text(FAKE_TOKEN_FILE, "utf-8")
    (directory / "client_secret_000000-test.apps.googleusercontent.com.json").write_text(
        json.dumps({"installed": {"client_id": "x", "client_secret": "FAKE-CLIENT-gggggg"}}),
        "utf-8",
    )
    # Derived data that should *not* travel.
    (directory / "youtube-channel-cache.json").write_text('{"id": "UC_x"}', "utf-8")


def empty_installation(tmp_path: Path) -> Settings:
    """A second computer: same app, nothing configured yet."""
    fresh = Settings(data_dir=tmp_path / "pc2")
    fresh.ensure_dirs()
    return fresh


# --- the export is sealed ---------------------------------------------------


class TestExportIsSealed:
    def test_no_secret_appears_in_the_exported_bytes(self, settings) -> None:
        seed_installation(settings)

        data, _name, _contents = export_bundle(BundleExportRequest(passphrase=PASSPHRASE))

        # The one assertion that matters most: a regression to a plain JSON dump
        # fails right here.
        for value in FAKE_SECRETS.values():
            assert value.encode("utf-8") not in data
        assert b"FAKE-TOKEN-eeeeeeeeeeee" not in data
        assert b"FAKE-REFRESH-ffffffffffff" not in data
        assert b"FAKE-CLIENT-gggggg" not in data
        assert b"elevenlabs_api_key" not in data

    def test_the_header_says_how_much_without_saying_what(self, settings) -> None:
        seed_installation(settings)

        data, name, contents = export_bundle(BundleExportRequest(passphrase=PASSPHRASE))

        document = json.loads(data)
        assert document["format"] == "evb-settings-bundle"
        assert document["cipher"] == "AES-256-GCM"
        assert document["kdf"]["name"] == "scrypt"
        assert contents.secrets == len(FAKE_SECRETS)
        # Three carried files; the channel cache is derived and stays behind.
        assert contents.credential_files == 3
        # Counts and a date, and nothing that names a service.
        assert set(document["contents"]) == {"settings", "secrets", "credentialFiles", "createdAt"}
        assert name.endswith(FILE_SUFFIX)

    def test_the_filename_is_not_json(self, settings) -> None:
        seed_installation(settings)

        _data, name, _contents = export_bundle(BundleExportRequest(passphrase=PASSPHRASE))

        # A bundle must never look like a config file someone skims and commits.
        assert not name.endswith(".json")

    def test_a_weak_passphrase_is_refused_before_anything_is_read(self, settings) -> None:
        seed_installation(settings)

        with pytest.raises(ValidationError) as excinfo:
            export_bundle(BundleExportRequest(passphrase="kısa"))

        assert excinfo.value.code.value == "settings_bundle_passphrase"
        assert excinfo.value.suggestion

    def test_credentials_can_be_left_out(self, settings) -> None:
        seed_installation(settings)

        _data, _name, contents = export_bundle(
            BundleExportRequest(passphrase=PASSPHRASE, include_credentials=False)
        )

        assert contents.credential_files == 0
        assert contents.secrets == len(FAKE_SECRETS)


# --- a round trip -----------------------------------------------------------


class TestRoundTrip:
    def test_keys_and_grants_arrive_on_the_other_machine(
        self, settings, tmp_path, monkeypatch
    ) -> None:
        seed_installation(settings)
        data, _name, _contents = export_bundle(BundleExportRequest(passphrase=PASSPHRASE))

        second = empty_installation(tmp_path)
        assert second.secret_names() == []

        result = import_bundle(data, PASSPHRASE, settings=second)

        assert sorted(result.secrets_imported) == sorted(FAKE_SECRETS)
        for name, value in FAKE_SECRETS.items():
            assert second.get_secret(name) == value
        assert sorted(result.credential_files_imported) == [
            "client_secret_000000-test.apps.googleusercontent.com.json",
            "meta-token.json",
            "youtube-upload-token.json",
        ]
        assert (second.oauth_secrets_dir / "meta-token.json").read_text("utf-8") == (
            FAKE_TOKEN_FILE
        )

    def test_restored_credential_files_are_owner_readable_only(
        self, settings, tmp_path
    ) -> None:
        seed_installation(settings)
        data, _name, _contents = export_bundle(BundleExportRequest(passphrase=PASSPHRASE))
        second = empty_installation(tmp_path)

        import_bundle(data, PASSPHRASE, settings=second)

        target = second.oauth_secrets_dir / "youtube-upload-token.json"
        assert stat.S_IMODE(target.stat().st_mode) == 0o600

    def test_preferences_travel_but_paths_do_not(self, settings, tmp_path) -> None:
        seed_installation(settings)
        data, _name, _contents = export_bundle(BundleExportRequest(passphrase=PASSPHRASE))
        second = empty_installation(tmp_path)

        result = import_bundle(data, PASSPHRASE, settings=second)

        assert result.settings_applied is True
        assert second.mutable.default_voice == "af_heart"
        assert second.mutable.object_storage_bucket == "evb-temp"
        # The other machine's project folder does not exist here, so it is not
        # adopted — and the result says so rather than staying quiet.
        assert second.mutable.projects_dir == ""
        assert "projects_dir" in result.skipped_settings
        assert result.warnings

    def test_paths_can_be_taken_deliberately(self, settings, tmp_path) -> None:
        seed_installation(settings)
        data, _name, _contents = export_bundle(BundleExportRequest(passphrase=PASSPHRASE))
        second = empty_installation(tmp_path)

        result = import_bundle(data, PASSPHRASE, include_paths=True, settings=second)

        assert second.mutable.projects_dir == "/Volumes/PC1/projects"
        assert result.skipped_settings == []
        # The path is adopted as asked, and the fact that this machine cannot
        # create it is reported rather than aborting an import that already
        # restored the keys.
        assert any("/Volumes/PC1/projects" in warning for warning in result.warnings)
        assert result.secrets_imported

    def test_existing_secrets_can_be_protected(self, settings, tmp_path) -> None:
        seed_installation(settings)
        data, _name, _contents = export_bundle(BundleExportRequest(passphrase=PASSPHRASE))
        second = empty_installation(tmp_path)
        second.set_secret("elevenlabs_api_key", "KEEP-THIS-ONE")

        result = import_bundle(data, PASSPHRASE, overwrite=False, settings=second)

        assert second.get_secret("elevenlabs_api_key") == "KEEP-THIS-ONE"
        assert "elevenlabs_api_key" in result.secrets_skipped
        assert "meta_app_secret" in result.secrets_imported

    def test_the_result_names_what_moved_and_never_its_value(
        self, settings, tmp_path
    ) -> None:
        seed_installation(settings)
        data, _name, _contents = export_bundle(BundleExportRequest(passphrase=PASSPHRASE))
        second = empty_installation(tmp_path)

        result = import_bundle(data, PASSPHRASE, settings=second)

        payload = result.model_dump_json()
        assert "meta_app_secret" in payload
        for value in FAKE_SECRETS.values():
            if value.startswith("FAKE-"):
                assert value not in payload


# --- refusals ---------------------------------------------------------------


class TestRefusals:
    def test_a_wrong_passphrase_changes_nothing(self, settings, tmp_path) -> None:
        seed_installation(settings)
        data, _name, _contents = export_bundle(BundleExportRequest(passphrase=PASSPHRASE))
        second = empty_installation(tmp_path)

        with pytest.raises(ValidationError) as excinfo:
            import_bundle(data, "yanlis-parola-buymus", settings=second)

        assert excinfo.value.code.value == "settings_bundle_passphrase"
        assert second.secret_names() == []
        assert list(second.oauth_secrets_dir.glob("*.json")) == []

    def test_an_edited_header_invalidates_the_whole_file(
        self, settings, tmp_path
    ) -> None:
        seed_installation(settings)
        data, _name, _contents = export_bundle(BundleExportRequest(passphrase=PASSPHRASE))
        document = json.loads(data)
        # The header is the AEAD's associated data, so lying about the counts
        # must break the file rather than change how it opens.
        document["contents"]["secrets"] = 0
        tampered = json.dumps(document).encode("utf-8")
        second = empty_installation(tmp_path)

        with pytest.raises(ValidationError):
            import_bundle(tampered, PASSPHRASE, settings=second)

        assert second.secret_names() == []

    def test_an_unrelated_file_is_named_as_such(self, settings) -> None:
        with pytest.raises(ValidationError) as excinfo:
            import_bundle(b'{"hello": "world"}', PASSPHRASE)

        assert excinfo.value.code.value == "settings_bundle_invalid"
        assert FILE_SUFFIX in excinfo.value.suggestion

    def test_a_file_that_is_not_json_at_all_is_named_as_such(self, settings) -> None:
        with pytest.raises(ValidationError) as excinfo:
            import_bundle(b"\x00\x01\x02not a bundle", PASSPHRASE)

        assert excinfo.value.code.value == "settings_bundle_invalid"

    def test_a_newer_format_asks_for_an_update(self, settings) -> None:
        seed_installation(settings)
        data, _name, _contents = export_bundle(BundleExportRequest(passphrase=PASSPHRASE))
        document = json.loads(data)
        document["version"] = 99

        with pytest.raises(ValidationError) as excinfo:
            import_bundle(json.dumps(document).encode("utf-8"), PASSPHRASE)

        assert "güncelleyin" in excinfo.value.suggestion

    def test_a_credential_name_from_the_file_cannot_escape_the_folder(
        self, settings, tmp_path, monkeypatch
    ) -> None:
        """The names inside a bundle are untrusted input, not trusted metadata."""
        seed_installation(settings)
        import app.config_bundle as bundle_module

        # Smuggle hostile names into the payload at the point it is built.
        original = bundle_module._read_credential_files
        monkeypatch.setattr(
            bundle_module,
            "_read_credential_files",
            lambda s: {
                **original(s),
                "../../evil.json": "owned",
                "meta-token.json.sh": "owned",
                ".bashrc": "owned",
            },
        )
        data, _name, _contents = export_bundle(BundleExportRequest(passphrase=PASSPHRASE))
        second = empty_installation(tmp_path)

        result = import_bundle(data, PASSPHRASE, settings=second)

        assert "../../evil.json" in result.credential_files_skipped
        assert "meta-token.json.sh" in result.credential_files_skipped
        assert ".bashrc" in result.credential_files_skipped
        assert not (second.data_dir.parent / "evil.json").exists()
        assert not (second.oauth_secrets_dir / ".bashrc").exists()


class TestHeaderPreview:
    def test_the_contents_can_be_read_without_the_passphrase(self, settings) -> None:
        seed_installation(settings)
        data, _name, _contents = export_bundle(BundleExportRequest(passphrase=PASSPHRASE))

        contents = read_bundle_header(data)

        assert contents.secrets == len(FAKE_SECRETS)
        assert contents.credential_files == 3
        assert contents.created_at is not None


# --- the HTTP surface -------------------------------------------------------


class TestEndpoints:
    def test_export_returns_a_downloadable_sealed_file(self, client, settings) -> None:
        seed_installation(settings)

        response = client.post("/api/settings/export", json={"passphrase": PASSPHRASE})

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/octet-stream"
        assert FILE_SUFFIX in response.headers["content-disposition"]
        assert response.headers["x-evb-bundle-secrets"] == str(len(FAKE_SECRETS))
        for value in FAKE_SECRETS.values():
            assert value.encode("utf-8") not in response.content

    def test_export_refuses_a_weak_passphrase(self, client, settings) -> None:
        response = client.post("/api/settings/export", json={"passphrase": "abc"})

        assert response.status_code == 422
        assert response.json()["code"] == "settings_bundle_passphrase"

    def test_inspect_reports_the_counts_without_a_passphrase(
        self, client, settings
    ) -> None:
        seed_installation(settings)
        exported = client.post("/api/settings/export", json={"passphrase": PASSPHRASE})

        response = client.post(
            "/api/settings/import/inspect",
            files={"file": ("bundle.evbkey", exported.content, "application/octet-stream")},
        )

        assert response.status_code == 200
        assert response.json()["secrets"] == len(FAKE_SECRETS)

    def test_import_reports_names_and_restores_the_keys(self, client, settings) -> None:
        seed_installation(settings)
        exported = client.post("/api/settings/export", json={"passphrase": PASSPHRASE})
        for name in FAKE_SECRETS:
            settings.set_secret(name, None)
        assert settings.secret_names() == []

        response = client.post(
            "/api/settings/import",
            files={"file": ("bundle.evbkey", exported.content, "application/octet-stream")},
            data={"passphrase": PASSPHRASE, "overwrite": "true", "includePaths": "false"},
        )

        assert response.status_code == 200
        body = response.json()
        assert "meta_app_secret" in body["secretsImported"]
        assert settings.get_secret("meta_app_secret") == FAKE_SECRETS["meta_app_secret"]
        # Names, never values.
        for value in FAKE_SECRETS.values():
            if value.startswith("FAKE-"):
                assert value not in response.text

    def test_import_with_a_wrong_passphrase_is_a_clear_422(
        self, client, settings
    ) -> None:
        seed_installation(settings)
        exported = client.post("/api/settings/export", json={"passphrase": PASSPHRASE})

        response = client.post(
            "/api/settings/import",
            files={"file": ("bundle.evbkey", exported.content, "application/octet-stream")},
            data={"passphrase": "bu-parola-yanlis"},
        )

        assert response.status_code == 422
        assert response.json()["code"] == "settings_bundle_passphrase"
        assert response.json()["suggestion"]
