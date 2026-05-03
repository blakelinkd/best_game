from typing import Dict, List, Optional


def calculate_stream_viewer_stats(stream_viewer_counts: List[int]) -> Dict[str, float]:
    counts = sorted([max(0, int(count or 0)) for count in stream_viewer_counts], reverse=True)
    stream_count = len(counts)
    viewer_count = sum(counts)

    if stream_count == 0:
        return {
            "viewer_count": 0,
            "stream_count": 0,
            "average_viewers_per_stream": 0.0,
            "median_viewers_per_stream": 0.0,
            "adjusted_average_viewers_per_stream": 0.0,
            "top_stream_viewer_count": 0,
            "top_stream_viewer_share": 0.0,
            "whale_adjusted_viewer_count": 0,
            "whale_adjusted_stream_count": 0,
        }

    ascending = list(reversed(counts))
    midpoint = stream_count // 2
    if stream_count % 2:
        median = float(ascending[midpoint])
    else:
        median = (ascending[midpoint - 1] + ascending[midpoint]) / 2

    top_stream_viewer_count = counts[0]
    top_stream_viewer_share = top_stream_viewer_count / viewer_count if viewer_count else 0.0

    adjusted_counts = counts
    if stream_count >= 5:
        trim_count = max(1, int(stream_count * 0.1))
        adjusted_counts = counts[trim_count:]
    elif stream_count >= 2 and top_stream_viewer_share >= 0.7 and top_stream_viewer_count >= 25:
        adjusted_counts = counts[1:]

    adjusted_viewer_count = sum(adjusted_counts)
    adjusted_stream_count = len(adjusted_counts)
    adjusted_average = adjusted_viewer_count / adjusted_stream_count if adjusted_stream_count else 0.0

    return {
        "viewer_count": viewer_count,
        "stream_count": stream_count,
        "average_viewers_per_stream": round(viewer_count / stream_count, 2),
        "median_viewers_per_stream": round(median, 2),
        "adjusted_average_viewers_per_stream": round(adjusted_average, 2),
        "top_stream_viewer_count": top_stream_viewer_count,
        "top_stream_viewer_share": round(top_stream_viewer_share, 3),
        "whale_adjusted_viewer_count": adjusted_viewer_count,
        "whale_adjusted_stream_count": adjusted_stream_count,
    }


def calculate_discovery_score(
    viewer_count: int,
    stream_count: int,
    adjusted_average_viewers_per_stream: Optional[float] = None,
    median_viewers_per_stream: Optional[float] = None,
    top_stream_viewer_share: Optional[float] = None,
) -> float:
    """
    Score a Twitch category by how likely a 0-follower streamer is to reach ~2
    concurrent viewers through organic browse discovery.

    Factors (all weighted 0-1):
    - Visibility: 12 / streams  — exponential decay models how far viewers
      typically scroll past before your thumbnail disappears.
    - Demand: effective-averages / 5  — the deeper the average non-whale
      stream, the more viewers are browsing (capped at 5).
    - Audience floor: total_viewers / (streams × 2, min 10)  — there must be
      enough total viewers for every streamer to average 2+.
    - Balance:  1 − (top_stream_share − 0.5) / 0.5  — penalise categories
      where one channel hoards >50 % of the audience.
    """
    # Visibility: organic browse drops off rapidly after ~12 thumbnails
    discoverability = 12.0 / max(float(stream_count), 12.0)

    raw_average = viewer_count / max(stream_count, 1)
    effective_average = (
        float(adjusted_average_viewers_per_stream)
        if adjusted_average_viewers_per_stream is not None
        else raw_average
    )
    # Blend mean and median equally — the median is a better predictor of
    # what the *typical* small streamer gets than the (whale-skewed) mean.
    if median_viewers_per_stream is not None:
        effective_average = max(effective_average * 0.5 + float(median_viewers_per_stream) * 0.5, 0.0)
    demand = min(effective_average / 5, 1.0)

    # Continuous floor: total viewers must be deep enough for every streamer
    # to average 2+ concurrent viewers (minimum 10 so borderline-niche
    # categories aren't flattered by tiny numbers).
    needed_viewers = max(float(stream_count) * 2.0, 10.0)
    audience_floor = min(float(viewer_count) / needed_viewers, 1.0)

    share = max(0.0, min(float(top_stream_viewer_share or 0.0), 1.0))
    balance = 1.0 - max(0.0, (share - 0.5) / 0.5)

    score = discoverability * 0.45 + demand * 0.35 + audience_floor * 0.1 + balance * 0.1
    return round(score, 3)
