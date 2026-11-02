ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache samba samba-common-tools bash procps shadow

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ /app/
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
