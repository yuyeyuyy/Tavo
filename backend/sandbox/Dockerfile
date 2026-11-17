FROM python:3.12-slim
LABEL tavo.sandbox.version="3"
COPY requirements.lock /opt/tavo/requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes -r /opt/tavo/requirements.lock
COPY runtime/ /usr/local/lib/python3.12/site-packages/
RUN groupadd --gid 10001 agent && useradd --uid 10001 --gid agent --no-create-home agent \
    && mkdir -p /workspace /source /run/tavo-egress && chown 10001:10001 /workspace /source /run/tavo-egress
USER 10001:10001
WORKDIR /workspace
ENV HOME=/tmp LANG=C.UTF-8
CMD ["/bin/bash", "--noprofile", "--norc"]
