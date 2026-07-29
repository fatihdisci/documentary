"""Publishing: taking a finished video and putting it on a platform.

Four destinations, four independent jobs: YouTube, an Instagram Reel, a Facebook
Page Reel, and a TikTok Direct Post. Each keeps its own history entry and its own
duplicate protection, so a failure on one never causes an upload anywhere else.

One module per platform owns every network call it makes — ``youtube.py``,
``meta.py`` (Instagram and Facebook share a single Meta grant) and ``tiktok.py``
— plus ``hosting.py``, which exists only because Meta's APIs download the video
from a URL rather than accepting the file.

No module here may return, log or embed a credential; the tokens live in the
app's secrets directory and are read only by the module that owns them.
"""
