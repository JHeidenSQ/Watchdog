"""Spark ALCOR adapter: the only laser calls the watchdog makes.

LINK is a serial port (COM3, /dev/cu.usbserial-X) or host:port.
Fill in the calls marked VENDOR from Spark's Python library.
"""


def is_serial(link):
    return link.upper().startswith("COM") or link.startswith("/dev/")


class Alcor:
    def __init__(self, link):
        self.link = link
        self.dev = None

    def __enter__(self):
        # VENDOR: open the link, e.g.
        #   self.dev = sparklib.Alcor(port=self.link) if is_serial(self.link)
        #              else sparklib.Alcor(host=..., port=...)
        raise NotImplementedError("wire Spark's library into alcor.py")
        return self

    def __exit__(self, *exc):
        if self.dev is not None:
            self.dev.close()  # VENDOR

    def emission(self):
        return bool(self.dev.get_emission())  # VENDOR

    def set_emission(self, on):
        self.dev.set_emission(on)  # VENDOR

    def mode(self):
        # opaque to the watchdog: whatever set_mode accepts back (alignment / full power)
        return self.dev.get_power_mode()  # VENDOR

    def set_mode(self, mode):
        self.dev.set_power_mode(mode)  # VENDOR
