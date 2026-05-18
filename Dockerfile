FROM python:3.11-alpine3.23

ARG WITH_PLUGINS=true

ENV PACKAGES=/usr/local/lib/python3.11/site-packages
ENV PYTHONDONTWRITEBYTECODE=1

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

# Add custom mkdocs_drawio plugin
COPY plugins/*.whl ./plugins/
RUN pip install --no-cache-dir ./plugins/*.whl

# Add scripts
COPY scripts/get-included-repos.sh /usr/local/bin/get-included-repos
COPY scripts/docker-entrypoint.sh /docker-entrypoint.sh

RUN chmod +x \
    /usr/local/bin/get-included-repos \
    /docker-entrypoint.sh

WORKDIR /docs

EXPOSE 8000

ENTRYPOINT ["/sbin/tini", "--", "/docker-entrypoint.sh"]
CMD ["serve", "--dev-addr=0.0.0.0:8000"]