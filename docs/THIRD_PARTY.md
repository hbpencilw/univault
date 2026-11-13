# Third-party projects

Development referenced the following public projects. Only **MIT-licensed**
code may be reused with attribution; GPL/AGPL/PolyForm projects are treated as
behavioural specifications only, and their source is never copied or vendored
into this repository.

| Project | Licence | Use in UniVault |
|---|---|---|
| moodle-dl | GPL-3.0 | protocol knowledge (token/launch flow, hashing) — concepts only |
| moodle-downloader (Oxford) | GPL-3.0-or-later | cookie + `sesskey` AJAX behaviour — concepts only |
| moodle-downloader (UNSW) | MIT | download/index patterns (reimplemented) |
| moodle2ia | MIT | Moodle 4.x course-tree parsing ideas |
| Moodle-Agent | MIT | persistent-profile login, week grouping, Panopto notes |
| monash-moodle-downloader | AGPL-3.0 | Monash selectors/Panopto behaviour — concepts only |
| moodle-buddy | AGPL-3.0 | seen-set diffing — concepts only |
| keats-downloader | MIT | collapsed-section handling |
| moodle-downloader-master | MIT | archived MV2 extension (reference only) |
| moodle-mcp | MIT | tool contracts for a future MCP layer |
| unsw-mcp | MIT | MCP read/write safety patterns |
| moodle-archive | PolyForm NC | version-history model — concepts only |
| muster / Muster-main | PolyForm NC | UX and cookie hand-off ideas — concepts only |
| edstem-cli | MIT | Ed CLI usage patterns |

UniVault itself is MIT-licensed (see `LICENSE`). If you redistribute it, keep
this notice and the upstream licences of any dependency you add.

## Icons

README visuals use four [Lucide](https://lucide.dev) icons
(`messages-square`, `graduation-cap`, `braces`, `git-merge`) under the ISC
License:

```text
ISC License

Copyright (c) 2026 Lucide Icons and Contributors

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
```

Lucide also distributes a subset of icons derived from Feather (MIT, Copyright
(c) 2013-present Cole Bemis); see the upstream `LICENSE` for the full list.
The four icons used here are not part of that Feather subset.
