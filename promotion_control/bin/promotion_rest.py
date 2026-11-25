"""
Persistent REST handler for /services/data_promotion (restmap.conf). splunkd keeps this
process running and sends each request as JSON; the work is done in promotion_api.py.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from splunk.persistconn.application import PersistentServerConnectionApplication  # noqa: E402

import promotion_api  # noqa: E402


class PromotionRest(PersistentServerConnectionApplication):
    def __init__(self, command_line=None, command_arg=None):
        PersistentServerConnectionApplication.__init__(self)

    def handle(self, in_string):
        if isinstance(in_string, bytes):
            in_string = in_string.decode("utf-8")
        return promotion_api.handle_request(json.loads(in_string))
