FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# Mount a folder holding your CSVs at /data and write reports there.
WORKDIR /data
ENTRYPOINT ["ledgermatch"]
CMD ["--help"]
