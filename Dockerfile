# syntax=docker/dockerfile:1.7

ARG PHP_VERSION=8.2
FROM composer:2 AS vendor
WORKDIR /app
COPY composer.json ./
RUN composer install --no-dev --optimize-autoloader --no-interaction --no-progress

FROM php:${PHP_VERSION}-apache
WORKDIR /var/www/html

# Install useful PHP extensions
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        curl \
        libicu-dev \
        libzip-dev \
    ; \
    docker-php-ext-install intl opcache; \
    rm -rf /var/lib/apt/lists/*

ENV RELAYCAT_DATA_DIR=/var/lib/relaycat \
    APACHE_DOCUMENT_ROOT=/var/www/html

# Apache tweaks: honor document root env and disable default index
RUN set -eux; \
    sed -ri 's!/var/www/html!${APACHE_DOCUMENT_ROOT}!g' /etc/apache2/sites-available/000-default.conf /etc/apache2/apache2.conf; \
    a2enmod rewrite headers expires

# Copy app source
COPY . .
COPY --from=vendor /app/vendor ./vendor

RUN set -eux; \
    mkdir -p "$RELAYCAT_DATA_DIR"; \
    chown -R www-data:www-data "$RELAYCAT_DATA_DIR"; \
    find . -type d -print0 | xargs -0 chmod 755; \
    find . -type f -print0 | xargs -0 chmod 644

VOLUME ["/var/lib/relaycat"]
EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s CMD curl -f http://127.0.0.1/verify.php || exit 1
