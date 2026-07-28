"""Publishing: taking a finished video and putting it on a platform.

Only YouTube actually talks to a network here. Instagram, Facebook and TikTok
exist as draft fields and UI so the panel is shaped for them, but nothing in this
package will send them a request — see ``models.PublishingPlatform``.
"""
