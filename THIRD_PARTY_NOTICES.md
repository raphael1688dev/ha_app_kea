# Third-party software

This repository contains the packaging and the Web UI of an app for Home Assistant, released under the MIT License
(see `LICENSE`). The app installs and runs other people's software. None of it is copied into this repository, and each piece stays under its own license. This file
lists what ends up in the app's image and where its source and license can be found.

**This project is not affiliated with, endorsed by, or sponsored by Internet Systems Consortium (ISC) or by the
Home Assistant project and the Open Home Foundation, which owns the Home Assistant trademark.** "ISC", "Kea" and
"Home Assistant" are the names of their respective owners and are used here only to say what this software works with.

## The DHCP server

| Component | License | Source |
|---|---|---|
| ISC Kea (`kea-dhcp4`, `kea-dhcp6`) | MPL-2.0 | <https://github.com/isc-projects/kea> (installed from the Alpine Linux packages `kea-dhcp4` and `kea-dhcp6`; the exact version is whatever Alpine ships and is shown in the CI run summary) |

Kea is licensed under the Mozilla Public License 2.0. This repository does not contain Kea's source code and does not
modify it; it generates Kea configuration files and starts the Kea programs. If you redistribute a built image, the
MPL-2.0 obligations for Kea (making its source available) may apply to you as well; the upstream repository above and
Alpine's package sources are where that source is.

## The base image and system packages

| Component | License | Source |
|---|---|---|
| Home Assistant base image `ghcr.io/home-assistant/base` | Apache-2.0 (the image build); the software inside has its own licenses | <https://github.com/home-assistant/docker-base> |
| Alpine Linux and its packages (`bash`, `python3`, `py3-pip`, ...) | various open-source licenses, see each package | <https://pkgs.alpinelinux.org/> |
| s6-overlay (init system inside the base image) | ISC License ("Internet Systems Consortium license") | <https://github.com/just-containers/s6-overlay> |

## Python packages (Web UI)

Pinned in `kea/requirements.txt`; licenses as declared by each package.

| Package | License |
|---|---|
| annotated-doc | MIT |
| annotated-types | MIT |
| anyio | MIT |
| click | BSD-3-Clause |
| fastapi | MIT |
| h11 | MIT |
| idna | BSD-3-Clause |
| opentelemetry-api | Apache-2.0 |
| pydantic | MIT |
| pydantic-core (installed by pydantic) | MIT |
| starlette | BSD-3-Clause |
| typing-extensions | PSF-2.0 |
| typing-inspection | MIT |
| uvicorn | BSD-3-Clause |

Development and test tooling (`requirements-dev.txt`: pytest, httpx, pyyaml) is not part of the app's image.
