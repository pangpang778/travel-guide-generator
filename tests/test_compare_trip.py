import unittest

from scripts.compare_trip import build_bookings, render_html, summarize


class CompareTripTests(unittest.TestCase):
    def test_summarize_orders_shorter_train_time_first(self):
        research = {
            "request": {"origin": "宁波", "destinations": ["苏州", "重庆"]},
            "destinations": [
                {
                    "destination": "重庆",
                    "transport": {
                        "outbound": {"trains": [{"duration": "13:24"}]},
                        "return": {"trains": [{"duration": "12:41"}]},
                    },
                    "xiaohongshu": {"notes": []},
                    "warnings": [],
                },
                {
                    "destination": "苏州",
                    "transport": {
                        "outbound": {"trains": [{"duration": "02:48"}]},
                        "return": {"trains": [{"duration": "02:29"}]},
                    },
                    "xiaohongshu": {"notes": [{"title": "攻略", "likes": "10", "url": "https://example.com"}]},
                    "warnings": [],
                },
            ],
        }
        rows = summarize(research)
        self.assertEqual([row["destination"] for row in rows], ["苏州", "重庆"])
        self.assertEqual(rows[0]["reference_count"], 1)

    def test_summarize_generates_itinerary_days_matching_request(self):
        research = {
            "request": {"origin": "宁波", "destinations": ["苏州"], "days": 4, "start_date": "2026-10-01"},
            "destinations": [
                {
                    "destination": "苏州",
                    "transport": {
                        "outbound": {"trains": [{"code": "G1", "start_time": "06:14", "arrive_time": "09:02", "duration": "02:48"}]},
                        "return": {"trains": [{"code": "G2", "start_time": "15:00", "arrive_time": "17:30", "duration": "02:30"}]},
                    },
                    "xiaohongshu": {"notes": []},
                    "warnings": [],
                }
            ],
        }
        rows = summarize(research)
        itinerary = rows[0]["itinerary"]
        self.assertEqual(len(itinerary), 4)
        self.assertEqual(itinerary[0]["date"], "2026-10-01")
        self.assertEqual(itinerary[3]["date"], "2026-10-04")
        first_slot = itinerary[0]["slots"][0]
        self.assertIn("G1", first_slot["title"])
        self.assertFalse(first_slot["pending"])
        last_day_slots = itinerary[3]["slots"]
        self.assertIn("G2", last_day_slots[-1]["title"])

    def test_summarize_marks_missing_return_train_pending(self):
        research = {
            "request": {"origin": "宁波", "destinations": ["成都"], "days": 3},
            "destinations": [
                {
                    "destination": "成都",
                    "transport": {
                        "outbound": {"message": "暂无直达车次"},
                        "return": {"message": "暂无直达车次"},
                    },
                    "xiaohongshu": {"notes": []},
                    "warnings": [],
                }
            ],
        }
        rows = summarize(research)
        itinerary = rows[0]["itinerary"]
        self.assertEqual(len(itinerary), 3)
        first_slot = itinerary[0]["slots"][0]
        self.assertTrue(first_slot["pending"])
        self.assertIn("待核验", first_slot["time"])
        last_day_slots = itinerary[2]["slots"]
        self.assertTrue(last_day_slots[-1]["pending"])

    def test_build_bookings_one_row_per_item(self):
        result = {
            "transport": {
                "outbound": {"trains": [{"code": "G1", "start_time": "06:14", "arrive_time": "09:02", "available": True, "second_seat": "¥200"}]},
                "return": {},
            },
            "references": {
                "xiaohongshu": {
                    "notes": [
                        {"title": "苏州攻略", "url": "https://example.com", "detail": [{"field": "content", "value": "苏博需要提前预约，园林门票注意提前购买，住宿选姑苏区"}]}
                    ]
                }
            },
        }
        bookings = build_bookings(result)
        items = [row["item"] for row in bookings]
        self.assertEqual(items, ["去程车票", "返程车票", "景点预约 / 门票", "住宿"])
        self.assertEqual(bookings[0]["status"], "可售")
        self.assertEqual(bookings[1]["status"], "待核验")
        self.assertEqual(len({row["item"] for row in bookings}), len(bookings))

    def test_render_html_contains_itinerary_switcher_and_pending_marks(self):
        research = {
            "request": {
                "origin": "宁波",
                "destinations": ["苏州", "重庆"],
                "start_date": "2026-09-26",
                "return_date": "2026-09-28",
                "days": 3,
                "nights": 2,
                "travelers": 1,
            },
            "destinations": [
                {
                    "destination": "苏州",
                    "transport": {
                        "outbound": {"trains": [{"code": "G1", "duration": "02:48"}]},
                        "return": {"trains": [{"code": "G2", "duration": "02:29"}]},
                    },
                    "xiaohongshu": {
                        "notes": [
                            {
                                "title": "苏州攻略",
                                "likes": "10",
                                "url": "https://www.xiaohongshu.com/note",
                            }
                        ]
                    },
                    "warnings": [],
                },
                {
                    "destination": "重庆",
                    "transport": {
                        "outbound": {"message": "暂无直达车次"},
                        "return": {"message": "暂无直达车次"},
                    },
                    "xiaohongshu": {"notes": []},
                    "warnings": [],
                },
            ],
        }
        html = render_html(research)
        self.assertIn("苏州攻略", html)
        self.assertIn("https://www.xiaohongshu.com/note", html)
        self.assertIn("data-cand-tab", html)
        self.assertIn("data-cand-panel", html)
        self.assertIn("待核验", html)
        self.assertIn("day-card", html)
        self.assertIn("booking-table", html)
        self.assertNotIn("{title}", html)
        self.assertNotIn("{panels}", html)

    def test_render_html_contains_candidate_and_source_link(self):
        research = {
            "request": {
                "origin": "宁波",
                "destinations": ["苏州"],
                "start_date": "2026-09-26",
                "return_date": "2026-09-28",
                "days": 3,
                "nights": 2,
                "travelers": 1,
            },
            "destinations": [
                {
                    "destination": "苏州",
                    "transport": {
                        "outbound": {"trains": [{"code": "G1", "duration": "02:48"}]},
                        "return": {"trains": [{"code": "G2", "duration": "02:29"}]},
                    },
                    "xiaohongshu": {
                        "notes": [
                            {
                                "title": "苏州攻略",
                                "likes": "10",
                                "url": "https://www.xiaohongshu.com/note",
                            }
                        ]
                    },
                    "warnings": [],
                }
            ],
        }
        html = render_html(research)
        self.assertIn("苏州", html)
        self.assertIn("苏州攻略", html)
        self.assertIn("https://www.xiaohongshu.com/note", html)
        self.assertNotIn("{title}", html)

    def test_render_html_links_each_candidate_guide(self):
        research = {
            "request": {
                "origin": "宁波",
                "destinations": ["苏州", "福州"],
                "start_date": "2026-09-26",
                "days": 3,
            },
            "destinations": [
                {"destination": "苏州", "transport": {}, "guide_html": "guides/01-trip.html"},
                {"destination": "福州", "transport": {}, "guide_html": "guides/02-trip.html"},
            ],
        }

        html = render_html(research)

        self.assertIn('href="guides/01-trip.html"', html)
        self.assertIn('href="guides/02-trip.html"', html)


if __name__ == "__main__":
    unittest.main()
