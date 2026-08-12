"""
Generates additional fictitious clients (beyond the default Aurora Home Goods)
using the same reusable generator as generate_sample_data.py — different
scale, trend direction, SEO health, and team size, so there's real variety to
test onboarding/reports against instead of one single story.

Each client writes to its own subdirectory: sample_data/clients/<slug>/
  - solstice-outdoor/   : growing DTC outdoor gear brand, strong SEO health
  - bramblewood-pet/    : stable/flat DTC pet supplies brand, moderate SEO debt

Run: python sample_data/generate_multi_client_data.py
"""
from datetime import date, timedelta
from pathlib import Path

from generate_sample_data import generate_client_dataset, write_client_dataset

OUT = Path(__file__).parent / "clients"
START = date(2026, 1, 1)
DAYS = 181
CHANNELS = ["Organic Search", "Paid Search", "Paid Social", "Email", "Direct", "Referral"]


def flat_trend(day_index: int, channel: str) -> float:
    return 1.0


def growth_trend(day_index: int, channel: str) -> float:
    # steady month-over-month growth across every channel, fastest on paid social —
    # strong enough to read clearly against the shared seasonal wave, not just offset it
    rate = 0.0055 if channel == "Paid Social" else 0.0035
    return 1.0 + day_index * rate


# ---------------------------------------------------------------------------
# Solstice Outdoor Co. — growing DTC outdoor/camping gear brand, healthy site
# ---------------------------------------------------------------------------
solstice = generate_client_dataset(
    seed=101, days=DAYS, start=START, channels=CHANNELS,
    base_sessions={"Organic Search": 480, "Paid Search": 410, "Paid Social": 390,
                    "Email": 160, "Direct": 220, "Referral": 70},
    conv_rate={"Organic Search": 0.024, "Paid Search": 0.031, "Paid Social": 0.022,
               "Email": 0.045, "Direct": 0.033, "Referral": 0.021},
    aov={"Organic Search": 142, "Paid Search": 128, "Paid Social": 118,
         "Email": 156, "Direct": 168, "Referral": 134},
    trend_fn=growth_trend, n_urls=160, seo_issue_rate=-0.03,  # negative = healthier than baseline
    seasonal_amplitude=0.06,  # weak seasonality so the growth trend reads clearly
    reps=["Casey Nguyen", "Reese Alvarado", "Dakota Kim"],
    products=["Trailhead 3-Season Tent", "Summit Down Jacket", "Basecamp Cook Kit",
              "Ridgeline Backpack 45L", "AllWeather Sleep System"],
    regions=["Northeast", "Mountain West", "Pacific", "South"],
    lead_sources=["Organic Search", "Paid Search", "Paid Social", "Email", "Referral", "Trade Show"],
    lead_source_weights=[0.24, 0.22, 0.24, 0.14, 0.11, 0.05],
    win_rate=0.74, deal_rate=2.6, deal_amount_mean_log=6.6,
    url_slug_pool=["3-season-tent", "down-jacket", "cook-kit", "backpack-45l", "sleep-system",
                   "trekking-poles", "camp-hammock", "water-filter", "headlamp-rechargeable",
                   "insulated-bottle", "trail-runners", "rain-shell"],
    url_prefix="solsticeoutdoor",
)
write_client_dataset(solstice, OUT / "solstice-outdoor")

# ---------------------------------------------------------------------------
# Bramblewood Pet Supplies — stable/flat DTC pet products, moderate SEO debt
# ---------------------------------------------------------------------------
bramblewood = generate_client_dataset(
    seed=202, days=DAYS, start=START, channels=CHANNELS,
    base_sessions={"Organic Search": 340, "Paid Search": 180, "Paid Social": 210,
                    "Email": 260, "Direct": 150, "Referral": 130},
    conv_rate={"Organic Search": 0.026, "Paid Search": 0.030, "Paid Social": 0.018,
               "Email": 0.052, "Direct": 0.031, "Referral": 0.038},
    aov={"Organic Search": 42, "Paid Search": 39, "Paid Social": 36,
         "Email": 48, "Direct": 44, "Referral": 46},
    trend_fn=flat_trend, n_urls=140, seo_issue_rate=0.05,  # positive = more issues than baseline
    seasonal_amplitude=0.0,  # genuinely flat — no seasonal wave to muddy the story
    reps=["Harper Boyd", "Emerson Vance"],
    products=["Grain-Free Kibble 15lb", "Salmon Dental Chews", "Cozy Cave Bed",
              "Adjustable Walk Harness", "Catnip Enrichment Set"],
    regions=["Northeast", "Midwest", "South", "West"],
    lead_sources=["Organic Search", "Paid Search", "Paid Social", "Email", "Referral", "Trade Show"],
    lead_source_weights=[0.22, 0.12, 0.14, 0.30, 0.18, 0.04],
    win_rate=0.61, deal_rate=1.8, deal_amount_mean_log=5.4,
    url_slug_pool=["grain-free-kibble", "dental-chews", "cozy-cave-bed", "walk-harness",
                   "enrichment-set", "puppy-training-pads", "cat-scratcher-post", "travel-carrier",
                   "slow-feeder-bowl", "waterproof-mat"],
    url_prefix="bramblewoodpet",
)
write_client_dataset(bramblewood, OUT / "bramblewood-pet")

print("Done — 2 new clients written under sample_data/clients/")
