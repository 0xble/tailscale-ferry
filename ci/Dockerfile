# Trusted prerequisite image. Candidate source enters separately through the runner.
# Both immutable Bookworm images pin Python, Go and the Git/C build toolchain.
FROM python:3.11.14-slim-bookworm@sha256:65a93d69fa75478d554f4ad27c85c1e69fa184956261b4301ebaf6dbb0a3543d AS python
FROM golang:1.27.1-bookworm@sha256:69a7b9788769bec032d238959b61854e9ae87f57be9029ec04e9885fabf99195

# Go's buildpack-deps base already contains Git, gcc, libc headers, make and
# procps. Copy the exact Python installation without a floating apt transaction.
COPY --from=python /usr/local/ /usr/local/
RUN test "$(python3 --version)" = 'Python 3.11.14' \
    && test "$(go env GOVERSION)" = 'go1.27.1' \
    && python3 -c 'import bz2, ctypes, lzma, sqlite3, ssl, venv; print(ssl.OPENSSL_VERSION)' \
    && python3 -m pip --version \
    && git --version \
    && ps --version \
    && dpkg-query --show git gcc libc6-dev make procps \
    && printf 'int main(void) { return 0; }\n' | cc -x c - -o /tmp/ci-compiler-probe \
    && /tmp/ci-compiler-probe \
    && rm /tmp/ci-compiler-probe \
    && groupadd --gid 1000 runner \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash runner \
    && mkdir -p /workspace \
    && chown runner:runner /workspace

# Optional Playwright/browser system dependencies are deliberately separate.
# Contributors prepare that lane with bin/ci browser-setup on a supported host.
ENV HOME=/home/runner
WORKDIR /workspace
USER 1000:1000
