# Dependency notices

The MIT license at the project root covers the original tracker code. Dependencies
are installed separately; the tracker wheel contains no vendored dependency code.
Their licenses and copyright notices remain with their distributions and are not
replaced by the project's MIT license.

The runtime distributions selected by `uv.lock` for the validated environments are:

| Dependency | Version | Declared license |
| --- | --- | --- |
| httpx | 0.28.1 | BSD-3-Clause |
| httpcore | 1.0.9 | BSD-3-Clause |
| anyio | 4.15.1 | MIT |
| certifi | 2026.7.22 | MPL-2.0 |
| h11 | 0.16.0 | MIT |
| idna | 3.20 | BSD-3-Clause |
| typing_extensions | 4.16.0 | PSF-2.0 |
| tzdata | 2026.4 | Apache-2.0 |

The installed distributions include their license files under their `.dist-info`
directories. In particular, certifi includes its certificate-bundle notice, and
typing_extensions and tzdata include their additional license text. Keep those
notices if redistributing their code or data. Development tools have their own
licenses and are not included in the tracker wheel either.

This inventory records the inspected dependency metadata and notices; it does not
relicense third-party material. No conflicting ownership or license notice was
found in the tracked project sources during publication preparation.
