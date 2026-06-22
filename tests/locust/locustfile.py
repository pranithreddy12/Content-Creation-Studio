"""Locust load profile for the API.

Run:
    locust -f locustfile.py --host=http://api.staging.studio.example.com \
           --users 1000 --spawn-rate 50 --run-time 10m

Set BEARER_TOKEN env to a Clerk test JWT to exercise authed endpoints.
"""
from __future__ import annotations

import os
import random

from locust import HttpUser, between, task


class StudioUser(HttpUser):
    wait_time = between(1, 4)

    def on_start(self) -> None:
        token = os.getenv("BEARER_TOKEN")
        if token:
            self.client.headers["Authorization"] = f"Bearer {token}"

    @task(3)
    def health(self) -> None:
        self.client.get("/health")

    @task(2)
    def list_brands(self) -> None:
        self.client.get("/v1/brands", name="/v1/brands")

    @task(1)
    def list_assets(self) -> None:
        statuses = ["draft", "review", "published"]
        self.client.get(f"/v1/assets?status={random.choice(statuses)}", name="/v1/assets")

    @task(1)
    def list_workflows(self) -> None:
        self.client.get("/v1/workflows", name="/v1/workflows")
