"""Build a podcast RSS feed XML for a single episode (or playlist of
already-published episodes if a manifest exists).

This is a minimal valid RSS 2.0 with iTunes namespace — enough for Apple
Podcasts / Spotify / Pocket Casts to ingest.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone


def render_rss(
    *,
    show_title: str,
    show_description: str,
    show_link: str,
    show_image_url: str | None,
    author: str,
    episodes: list[dict],
) -> str:
    """`episodes` items: {title, description, audio_url, duration_seconds,
    pubdate (iso), guid, episode_number?, image_url?, chapter_markdown?}.
    """
    items_xml: list[str] = []
    for ep in episodes:
        title = html.escape(ep.get("title", ""))
        description = html.escape(ep.get("description", ""))
        audio_url = ep.get("audio_url", "")
        pubdate = ep.get("pubdate") or datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
        guid = html.escape(ep.get("guid") or audio_url)
        dur = int(ep.get("duration_seconds") or 0)
        dur_str = f"{dur // 3600:02d}:{(dur % 3600) // 60:02d}:{dur % 60:02d}"
        image = ep.get("image_url")
        chapters = ep.get("chapter_markdown")

        item = [f"    <item>",
                f"      <title>{title}</title>",
                f"      <description><![CDATA[{ep.get('description', '')}"
                + (f"\n\n{chapters}" if chapters else "") + "]]></description>",
                f"      <enclosure url=\"{html.escape(audio_url)}\" type=\"audio/mpeg\"/>",
                f"      <pubDate>{pubdate}</pubDate>",
                f"      <guid isPermaLink=\"false\">{guid}</guid>",
                f"      <itunes:duration>{dur_str}</itunes:duration>"]
        if image:
            item.append(f"      <itunes:image href=\"{html.escape(image)}\"/>")
        if ep.get("episode_number"):
            item.append(f"      <itunes:episode>{int(ep['episode_number'])}</itunes:episode>")
        item.append("    </item>")
        items_xml.append("\n".join(item))

    image_tag = (
        f"    <itunes:image href=\"{html.escape(show_image_url)}\"/>" if show_image_url else ""
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" '
        'xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" '
        'xmlns:content="http://purl.org/rss/1.0/modules/content/">\n'
        '  <channel>\n'
        f'    <title>{html.escape(show_title)}</title>\n'
        f'    <link>{html.escape(show_link)}</link>\n'
        f'    <description>{html.escape(show_description)}</description>\n'
        f'    <itunes:author>{html.escape(author)}</itunes:author>\n'
        f'    <language>pt-BR</language>\n'
        f'{image_tag}\n'
        + "\n".join(items_xml) + "\n"
        '  </channel>\n'
        '</rss>\n'
    )
