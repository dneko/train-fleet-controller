"""Dispose of test windows on the Tk thread before background tests start."""
import gc


def begin_tk(test):
    # Collect Tk cycles only at teardown on the UI thread, not inside a Tcl
    # destruction callback or a later discovery worker's allocation.
    if gc.isenabled():
        gc.disable()
        test.addCleanup(gc.enable)


def close_demo(test):
    app = test.app
    test.app = test.root = None
    if hasattr(test,'sup'):
        test.sup = None
    app.close()
    del app
    gc.collect()
