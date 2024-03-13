#!/usr/bin/env python

"""
"Quiet mode" example proxy

Allows a client to turn on "quiet mode" which hides chat messages
This client doesn't handle system messages, and assumes none of them contain chat messages
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

    def packet_upstream_chat_command(self, buff):
        command = buff.unpack_string()

        if command == "quiet":
            self.toggle_quiet_mode()
            buff.discard()

        else:
            buff.restore()
            self.upstream.send_packet("chat_command", buff.read())

    def packet_upstream_chat_message(self, buff):
        buff.save()
        chat_message = self.read_chat(buff, "upstream")
        self.logger.info(" >> %s" % chat_message)

        if chat_message.startswith("/quiet"):
            self.toggle_quiet_mode()

        elif self.quiet_mode and not chat_message.startswith("/"):
            # Don't let the player send chat messages in quiet mode
            msg = "Can't send messages while in quiet mode"
            self.send_system(msg)

        else:
            # Pass to upstream
            buff.restore()
            self.upstream.send_packet("chat_message", buff.read())

    def toggle_quiet_mode(self):
        # Switch mode
        self.quiet_mode = not self.quiet_mode

        action = self.quiet_mode and "enabled" or "disabled"
        msg = "Quiet mode %s" % action

        self.send_system(msg)

    def packet_downstream_chat_message(self, buff):
        chat_message = self.read_chat(buff, "downstream")
        self.logger.info(" :: %s" % chat_message)

        # All chat messages on 1.19+ are from players and should be ignored in quiet mode
        if self.quiet_mode and self.downstream.protocol_version >= 759:
            return

        # Ignore message that look like chat when in quiet mode
        if chat_message is not None and self.quiet_mode and chat_message.startswith("<"):
            return

        # Pass to downstream
        buff.restore()
        self.downstream.send_packet("chat_message", buff.read())

    def read_chat(self, buff, direction):
        buff.save()
        if direction == "upstream":
            p_text = buff.unpack_string()
            buff.discard()

            return p_text
        elif direction == "downstream":
            # 1.19.1+
            if self.downstream.protocol_version >= 760:
                p_signed_message = buff.unpack_signed_message()
                buff.unpack_varint()  # Filter result
                p_position = buff.unpack_varint()
                p_sender_name = buff.unpack_chat()

                buff.discard()

                if p_position not in (1, 2):  # Ignore system and game info messages
                    # Sender name is sent separately to the message text
                    return ":: <%s> %s" % (
                    p_sender_name, p_signed_message.unsigned_content or p_signed_message.body.message)

                return

            p_text = buff.unpack_chat().to_string()

            # 1.19+
            if self.downstream.protocol_version == 759:
                p_unsigned_text = buff.unpack_optional(lambda: buff.unpack_chat().to_string())
                p_position = buff.unpack_varint()
                buff.unpack_uuid()  # Sender UUID
                p_sender_name = buff.unpack_chat()
                buff.discard()

                if p_position not in (1, 2):  # Ignore system and game info messages
                    # Sender name is sent separately to the message text
                    return "<%s> %s" % (p_sender_name, p_unsigned_text or p_text)

            elif self.downstream.protocol_version >= 47:  # 1.8.x+
                p_position = buff.unpack('B')
                buff.discard()

                if p_position not in (1, 2) and p_text.strip():  # Ignore system and game info messages
                    return p_text

            else:
                return p_text

    def send_system(self, message):
        if self.downstream.protocol_version >= 760:  # 1.19.1+
            self.downstream.send_packet("system_message",
                               self.downstream.buff_type.pack_chat(message),
                               self.downstream.buff_type.pack('?', False))  # Overlay false to put in chat
        elif self.downstream.protocol_version == 759:  # 1.19
            self.downstream.send_packet("system_message",
                               self.downstream.buff_type.pack_chat(message),
                               self.downstream.buff_type.pack_varint(1))  # Type 1 for system chat message
        else:
            self.downstream.send_packet("chat_message",
                               self.downstream.buff_type.pack_chat(message),
                               self.downstream.buff_type.pack('B', 0),
                               self.downstream.buff_type.pack_uuid(UUID(int=0)))

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
