import unittest

from scripts.compare_trip import render_html, summarize


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
        self.assertEqual(rows[0]["xhs_count"], 1)

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


if __name__ == "__main__":
    unittest.main()
