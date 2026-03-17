#!/bin/sh
set -e

# Replace __PLACEHOLDER__ markers with runtime env var values.
# Using sed avoids any envsubst quoting/availability issues and leaves
# all nginx $variables untouched.
sed \
    -e "s|__EMAIL_PROCESSOR_URL__|${EMAIL_PROCESSOR_URL}|g" \
    -e "s|__NOTIF_RECEIVER_URL__|${NOTIF_RECEIVER_URL}|g" \
    /etc/nginx/templates/default.conf.template \
    > /etc/nginx/conf.d/default.conf

exec nginx -g 'daemon off;'
