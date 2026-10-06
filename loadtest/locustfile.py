"""
Load test for the Beetle ID game (issue #383). See loadtest/README.md.

Each simulated player signs in as one of the throw-away loadtest-NN accounts, opens the game home, then plays the feed
like a person: loads the beetle's photos, thinks for 3-10 seconds, answers (or now and then skips), and every so often
looks at the leaderboard or their expertise tree. Answers are deliberately cautious (subfamily only), as a new player's
would be.
"""
import itertools
import os
import random
import re

from locust import HttpUser, between, events, task

PLAYERS = int(os.environ.get("LOADTEST_PLAYERS", "50"))
PASSWORD = os.environ.get("LOADTEST_PASSWORD", "")
_numbers = itertools.cycle(range(1, PLAYERS + 1))


@events.test_start.add_listener
def _check_password(environment, **kwargs):
    if not PASSWORD:
        raise SystemExit("Set LOADTEST_PASSWORD to the password given to the loadtest-NN players.")


class Player(HttpUser):
    wait_time = between(3, 10)   # thinking time between answers

    def on_start(self):
        self.username = f"loadtest-{next(_numbers):02d}"
        self.client.get("/accounts/login/", name="login page")
        self.client.post("/accounts/login/", name="login", data={
            "username": self.username, "password": PASSWORD, "csrfmiddlewaretoken": self.csrf(), "next": "/game/",
        })
        self.client.get("/game/", name="game home")
        self.subfamilies = []
        self.round = self.item = None
        self.start_feed()

    def csrf(self):
        return self.client.cookies.get("csrftoken", "")

    def post_json(self, url, body, name):
        return self.client.post(url, json=body, name=name, headers={"X-CSRFToken": self.csrf(), "Referer": self.host + "/game/"})

    def start_feed(self):
        res = self.post_json("/game/api/start/", {"mode": "mixed"}, "game: start")
        if res.ok:
            data = res.json()
            self.round, self.item = data.get("round"), data.get("item")
            self.load_photos()

    def load_photos(self):
        # what the feed loads: each beetle's small crop, then its large one (the whole photo only when it is opened)
        for image in (self.item or {}).get("images", []):
            for size in ("small", "large"):
                if image.get(size):
                    self.client.get(image[size], name=f"beetle crop ({size})")

    def answer_body(self):
        body = {"index": self.item["index"], "elapsed_ms": random.randint(3000, 10000)}
        if random.random() < 0.1:
            body["skipped"] = True
            if self.item.get("mode") == "pair":
                body = {"index": self.item["index"], "pair_answer": "unsure"}
            return body
        if self.item.get("mode") == "pair":
            body["pair_answer"] = random.choice(["different", "subfamily"])
        elif self.item.get("mode") == "odd":
            body["pick"] = random.randrange(len(self.item.get("images") or [None]))
        elif self.item.get("mode") == "select":
            body["picks"] = sorted(random.sample(range(len(self.item.get("images") or [])), 3))
        else:
            if not self.subfamilies:
                res = self.client.get("/game/api/taxa/?rank=subfamily", name="game: name lists")
                self.subfamilies = [o["value"] for o in res.json().get("options", [])] if res.ok else []
            if not self.subfamilies:
                body["skipped"] = True
            else:
                body["subfamily"] = random.choice(self.subfamilies)
        return body

    @task(20)
    def answer(self):
        if not self.round or not self.item:
            self.start_feed()
            return
        res = self.post_json(f"/game/api/round/{self.round}/answer/", self.answer_body(), "game: answer")
        if not res.ok:
            self.start_feed()   # out of step (409) or finished: pick the feed up again, as the page does
            return
        data = res.json()
        if data.get("done"):
            self.round = self.item = None
            return
        self.round = data.get("round") or self.round
        self.item = data.get("item")
        self.load_photos()

    @task(1)
    def leaderboard(self):
        self.client.get("/game/leaderboard/", name="leaderboard")

    @task(1)
    def expertise(self):
        self.client.get("/game/expertise/", name="expertise")

    @task(1)
    def home(self):
        self.client.get("/game/", name="game home")
