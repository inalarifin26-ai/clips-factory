# core/queue.py
# Worker queue untuk proses video berat (opsional, bisa diaktifkan nanti)
# Bisa pakai celery + redis untuk job queue yang lebih robust

import asyncio
from collections import deque

job_queue = deque()

async def add_job(job):
    job_queue.append(job)

async def process_jobs():
    while True:
        if job_queue:
            job = job_queue.popleft()
            await job()
        await asyncio.sleep(1)
