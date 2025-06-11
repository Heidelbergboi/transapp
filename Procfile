# Procfile ─ Render will read this and launch your Flask app with Gunicorn
# ----------------------------------------------------------------------
# -w 2            two worker processes (good balance for CPU + I/O)
# --threads 4     each worker can handle 4 concurrent requests
# --timeout 600   allow up to 10 min for long /stream_raw jobs
# --keep-alive 120 keep the connection open while the browser streams logs
# --log-file -    send Gunicorn logs to stdout (Render captures them)
web: gunicorn app:app -w 2 --threads 4 --timeout 600 --keep-alive 120 --log-file -
