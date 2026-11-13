FROM python:3.11-slim
WORKDIR /app
COPY protocol.py client.py run_experiments.py analyze.py /app/
RUN mkdir -p /app/results
ENTRYPOINT ["python"]
CMD ["client.py", "--help"]
