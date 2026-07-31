"""Anonymized BookStack page fixture used by the converter tests.

The original "Emergency List: Vueko Backup with duply and rclone"
page mixes two HTML constructs the BookStack converter historically
struggles with:

1. `<pre><code class="language-...">` code blocks.  Default
   `html2text` renders those as 4-space indented code, which the
   markdown viewer does not treat as a fenced block, so the
   block looks empty in the rendered note.
2. `<details><summary>...</summary>...</details>` collapsible
   sections.  Default `html2text` strips the `<details>` wrapper
   and emits the body inline below the summary, so the content
   no longer sits inside the collapsible region.

The fixture page below is a Hunter-x-Hunter themed re-write of
the original (personal paths / hostnames / usernames have been
replaced with names from the series) so we can exercise both
paths against realistic BookStack HTML without leaking any real
private details.

The page is exposed as :data:`EMERGENCY_BACKUP_PAGE_HTML` plus
the full :data:`EMERGENCY_BACKUP_BOOK_PAYLOAD` so tests can either
feed it to :class:`BookstackHtmlConverter.html_to_markdown`
directly or build a zip and run the full orchestrator pipeline.
"""

from __future__ import annotations

# The Hunter-x-Hunter themed replacement page.  Mirrors the structure
# of the original "Vueko Backup" page: a short intro, a code block
# for the recovery one-liner, and three `<details>` collapsible
# sections (one of which itself contains a code block).
EMERGENCY_BACKUP_PAGE_HTML: str = (
    "<h1 id=\"bkmrk-emergency-list%3A-h\">Emergency List: Hunter Backup with duply and rclone</h1>"
    "<p id=\"bkmrk-keep-this-page-handy\">"
    "Keep this page handy.  When something on <code>whale-island</code> goes wrong, "
    "follow the steps below in order -- do not skip ahead.  All credentials live in "
    "<code>/etc/duplicity/zoldyck.conf</code> on <code>yorknew.ging-freecss.example</code>."
    "</p>"
    "<h2 id=\"bkmrk-1.-panic-recovery-on\">1. Panic recovery one-liner</h2>"
    "<p id=\"bkmrk-run-this-from-the-ki\">Run this from the killua host as the <code>gon</code> user:</p>"
    "<pre id=\"bkmrk-duply-yorknew-resto\"><code class=\"language-bash\">"
    "duply --socket /var/run/duplicity.sock zoldyck restore /tmp/zoldyck-restore &amp;&amp; "
    "echo &quot;restored at $(date -Iseconds)&quot; | tee -a /var/log/zoldyck.log"
    "</code></pre>"
    "<h2 id=\"bkmrk-2.-common-failure-mo\">2. Common failure modes</h2>"
    "<details id=\"bkmrk-gpg-key-rotated-on\">"
    "<summary>GPG key rotated on yorknew, restore keeps failing</summary>"
    "<p id=\"bkmrk-the-zoldyck-profile-p\">"
    "The <code>zoldyck</code> profile points at the old public key. "
    "Regenerate with:"
    "</p>"
    "<pre id=\"bkmrk-gpg---gen-key\"><code class=\"language-sh\">"
    "gpg --gen-key\n"
    "duply zoldyck import_secret_keys /root/zoldyck.sec"
    "</code></pre>"
    "<p id=\"bkmrk-then-verify-by-lis\">Then verify by listing the profile:</p>"
    "<pre id=\"bkmrk-duply-zoldyck-statu\"><code class=\"language-sh\">"
    "duply zoldyck status"
    "</code></pre>"
    "</details>"
    "<details id=\"bkmrk-rclone-can-not-re\">"
    "<summary>rclone cannot reach the dark-continent bucket</summary>"
    "<p id=\"bkmrk-check-the-following\">"
    "Check the following, in order:"
    "</p>"
    "<ol id=\"bkmrk-1.-rclone-config-sh\">"
    "<li><code>rclone config show dark-continent</code> returns a valid token.</li>"
    "<li><code>curl -sSI https://dark-continent.example/health</code> answers 200.</li>"
    "<li>The cron entry on <code>whale-island</code> still points at the new bucket name.</li>"
    "</ol>"
    "</details>"
    "<details id=\"bkmrk-disk-is-full-on-w\">"
    "<summary>Disk is full on whale-island, duply aborts before upload</summary>"
    "<p id=\"bkmrk-free-at-least-20-gb\">"
    "Free at least 20 GB under <code>/var/cache/duplicity</code> before retrying. "
    "If you cannot free enough space, run the rclone side manually:"
    "</p>"
    "<pre id=\"bkmrk-rclone-sync-var-ca\"><code class=\"language-sh\">"
    "rclone sync /var/cache/duplicity dark-continent:gon-backup --transfers 4 --checkers 8"
    "</code></pre>"
    "</details>"
)

# Minimal book payload wrapper around the page above.  The chapter
# carries the page so the orchestrator exercises the chapter branch
# of the pipeline; there is one trivial direct-child page so the
# "book pages" branch is also touched.
EMERGENCY_BACKUP_BOOK_PAYLOAD: dict = {
    "book": {
        "name": "Hunter Backup Drills",
        "description_html": "<p>Drills and recovery procedures for the Hunter backup chain.</p>",
        "cover": None,
        "chapters": [
            {
                "id": 1,
                "name": "Recovery drills",
                "description_html": "<p>What to do when the chain breaks.</p>",
                "priority": 0,
                "pages": [
                    {
                        "id": 100,
                        "name": "Emergency List: Hunter Backup with duply and rclone",
                        "html": EMERGENCY_BACKUP_PAGE_HTML,
                        "markdown": "",
                        "priority": 0,
                        "tags": [],
                    }
                ],
            }
        ],
        "pages": [
            {
                "id": 101,
                "name": "Quick reference",
                "html": "<p>See the recovery drills chapter.</p>",
                "markdown": "",
                "priority": 0,
                "tags": [],
            }
        ],
    }
}


__all__ = [
    "EMERGENCY_BACKUP_PAGE_HTML",
    "EMERGENCY_BACKUP_BOOK_PAYLOAD",
]