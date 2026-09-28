"""Locust users for heavier canary traffic:  locust -f sentinel/canary/locustfile.py --host http://localhost:8080"""

import random

from locust import HttpUser, between, task

from sentinel.canary.traffic import STUDENTS


class Student(HttpUser):
    wait_time = between(0.05, 0.3)

    def on_start(self):
        self.sid = random.choice(STUDENTS)

    @task(20)
    def profile(self):
        self.client.get(f"/students/{self.sid}", name="/students/[id]")

    @task(18)
    def gpa(self):
        self.client.get(f"/students/{self.sid}/gpa", name="/students/[id]/gpa")

    @task(12)
    def transcript(self):
        self.client.get(f"/students/{self.sid}/transcript", name="/students/[id]/transcript")

    @task(10)
    def fees(self):
        self.client.get(f"/students/{self.sid}/fees/2026-FALL", name="/students/[id]/fees")

    @task(5)
    def late_fee(self):
        self.client.post("/fees/late-fee", json={"amount": "25000", "due_date": "2026-09-01", "paid_on": "2026-09-12"})
