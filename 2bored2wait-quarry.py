#!/usr/bin/env python

"""
Idle proxy. Can be used afk idle on a server.
If you disconnect from the proxy it keeps the connection alive.
And you can later rejoin it again.
"""

import os.path
import json
from pathlib import Path
from typing import Optional

from quarry.net.auth import Profile, OfflineProfile
from twisted.internet import reactor
from quarry.types.uuid import UUID
from quarry.net.proxy import Downstream, DownstreamFactory, Bridge

class Credentials:
    display_name: str
    uuid: str
    client_token: str
    access_token: str

def get_credentials() -> Optional[Credentials]:
    accounts_file = f"{Path.home()}/.local/share/PrismLauncher/accounts.json"
    if not os.path.isfile(accounts_file):
        return None

    with open(accounts_file) as data:
        data = json.load(data)

    account = data['accounts'][0]
    creds = Credentials()

    # not sure
    creds.client_token = account['ygg']['token']
    creds.access_token = account['ygg']['token']

    # verified on https://mcuuid.net
    creds.uuid = account['profile']['id']
    creds.display_name = account['profile']['name']
    return creds

class QuietBridge(Bridge):
    quiet_mode = False

    def make_profile(self):
        credentials = get_credentials()
        if credentials is None:
            print("Failed to login falling back to offline profile")
            return OfflineProfile(self.downstream.display_name)
        print(f"logged in to account {credentials.display_name} {credentials.uuid}")

        return Profile(
                display_name=credentials.display_name,
                client_token=credentials.client_token,
                access_token=credentials.access_token,
                uuid=UUID.from_hex(credentials.uuid))

    def downstream_disconnected(self):
        """
        Called when the connection to the remote client is closed.
        """
        if self.upstream:
            print("Client disconnected. We stay connected.")


class Protocol2b2t(Downstream):
    def auth_ok(self, data):
        if data is None:
            print("Warning: got empty response from mojang session server")
            print("         trying to fallback to UUID from credentials ...")
            credentials = get_credentials()
            if credentials is None:
                raise RuntimeError("""
                The server responded with 204 to our hasJoined request
                so we did not get the uuid of the joined user
                and requesting the uuid from the credential helper failed as well

                There is a chance that it works if you try it again.
                Alternatively you try calling `get_credentials()` and debug
                why it does not find your login details.
                """)
            if credentials.uuid == "" or credentials.uuid is None:
                raise RuntimeError("""
                The server responded with 204 to our hasJoined request
                so we did not get the uuid of the joined user.

                As a fix you can include your UUID in the credentials file
                then it can be used as a fallback if the request to the
                mojang session server failed.
                """)
            data = {'id': credentials.uuid}
        return super().auth_ok(data)

class QuietDownstreamFactory(DownstreamFactory):
    protocol = Protocol2b2t
    bridge_class = QuietBridge
    motd = "Proxy Server"


def main(argv):
    # Parse options
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("-a", "--listen-host", default="", help="address to listen on")
    parser.add_argument("-p", "--listen-port", default=25565, type=int, help="port to listen on")
    parser.add_argument("-b", "--connect-host", default="127.0.0.1", help="address to connect to")
    parser.add_argument("-q", "--connect-port", default=25565, type=int, help="port to connect to")
    args = parser.parse_args(argv)

    # Create factory
    factory = QuietDownstreamFactory()
    factory.connect_host = args.connect_host
    factory.connect_port = args.connect_port

    # Listen
    factory.listen(args.listen_host, args.listen_port)
    reactor.run()


if __name__ == "__main__":
    import sys
    main(sys.argv[1:])
