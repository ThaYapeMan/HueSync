"""Test construction of the production canonical pipeline (no compatibility wrapper)."""
from huesync.spectrum_engine import make_spectrum_engine
from huesync.sync_engine import CanonicalAnalysisPipeline


def make_pipeline(*, source, bars=30, lower_cutoff_freq=50, higher_cutoff_freq=10000,
                  backend='v2', **settings):
    settings.pop('exertion_clip', None)  # rendering-only test parameter
    engine = make_spectrum_engine(backend, n_bars=bars,
                                  lower_hz=lower_cutoff_freq, upper_hz=higher_cutoff_freq)
    return CanonicalAnalysisPipeline(source=source, engine=engine, **settings)
