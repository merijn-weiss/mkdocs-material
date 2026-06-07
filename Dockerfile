FROM python:3.11-alpine3.23

ARG WITH_PLUGINS=true

ENV PACKAGES=/usr/local/lib/python3.11/site-packages
ENV PYTHONDONTWRITEBYTECODE=1

ENV MKDOCS_HOST=0.0.0.0
ENV MKDOCS_PORT=8000
ENV NO_MKDOCS_2_WARNING=true

WORKDIR /tmp

COPY material material
COPY package.json package.json
COPY pyproject.toml pyproject.toml
COPY README.md README.md
COPY *requirements.txt ./

RUN \
  apk upgrade --update-cache -a \
&& \
  apk add --no-cache \
    bash \
    cairo \
    freetype-dev \
    git \
    git-fast-import \
    jpeg-dev \
    openssh \
    pngquant \
    tini \
    zlib-dev \
&& \
  apk add --no-cache --virtual .build \
    gcc \
    g++ \
    libffi-dev \
    musl-dev \
&& \
  pip install --no-cache-dir --upgrade pip \
&& \
  pip install --no-cache-dir . \
&& \
  if [ "${WITH_PLUGINS}" = "true" ]; then \
    pip install --no-cache-dir \
      mkdocs-material[recommended] \
      mkdocs-material[git] \
      mkdocs-material[imaging]; \
  fi \
&& \
  if [ -e user-requirements.txt ]; then \
    pip install -U -r user-requirements.txt; \
  fi \
&& \
  apk del .build \
&& \
  for theme in mkdocs readthedocs; do \
    rm -rf ${PACKAGES}/mkdocs/themes/$theme; \
    ln -s \
      ${PACKAGES}/material/templates \
      ${PACKAGES}/mkdocs/themes/$theme; \
  done \
&& \
  rm -rf /tmp/* /root/.cache \
&& \
  find ${PACKAGES} \
    -type f \
    -path "*/__pycache__/*" \
    -exec rm -f {} \; \
&& \
  git config --system --add safe.directory '*' 

# Install Mondrian Docs framework
COPY mondrian /opt/mondrian

RUN \
  pip install --no-cache-dir \
    -r /opt/mondrian/requirements.txt \
&& \
  find /opt/mondrian/plugins \
    -name '*.whl' \
    -exec pip install --no-cache-dir {} \; \
&& \
  chmod +x /opt/mondrian/scripts/* \
&& \
  ln -sf \
    /opt/mondrian/scripts/mondrian-docs \
    /usr/local/bin/mondrian-docs

WORKDIR /docs

EXPOSE 8000

ENTRYPOINT ["/sbin/tini", "--", "/opt/mondrian/scripts/docker-entrypoint.sh"]
CMD ["serve"]