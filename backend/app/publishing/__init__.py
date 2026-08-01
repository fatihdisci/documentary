"""Publishing: taking a finished video and putting it on a platform.

Two destinations, two independent jobs: YouTube and a TikTok Direct Post. Each
keeps its own history entry and its own duplicate protection, so a failure on
one never causes an upload anywhere else.

One module per platform owns every network call it makes — ``youtube.py`` and
``tiktok.py``. Both platforms accept the video bytes directly from this
computer, so a render is never copied anywhere public on its way out.

No module here may return, log or embed a credential; the tokens live in the
app's secrets directory and are read only by the module that owns them.
"""
