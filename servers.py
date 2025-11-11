# Direct-query !servers plugin for Quake Live
# Queries servers directly without external API
# Shows server info + player names, scores, and Game State

import minqlx
import socket
import struct
import time

class servers(minqlx.Plugin):
    def __init__(self):
        super().__init__()
        self.add_command("servers", self.cmd_servers)

        self.set_cvar_once("qlx_servers", "")
        self.set_cvar_once("qlx_serversShowInChat", "0")

        self.last_time = None
        self.cooldown = 5  # seconds

    def cmd_servers(self, player, _msg, channel):
        if self.get_cvar("qlx_serversShowInChat", bool) and self.last_time is not None:
            cooldown_end = self.last_time + self.cooldown
            if time.time() < cooldown_end:
                secs_left = cooldown_end - time.time()
                player.tell("^7!servers cooldown: {:.1f}s".format(secs_left))
                return minqlx.RET_STOP_ALL

        servers_list = self.get_cvar("qlx_servers", list)
        if not servers_list or (len(servers_list) == 1 and not servers_list[0]):
            player.tell("qlx_servers is not set.")
            return minqlx.RET_STOP_ALL
        elif any(s.strip() == "" for s in servers_list):
            player.tell("qlx_servers has an invalid server (empty string).")
            return minqlx.RET_STOP_ALL

        irc = isinstance(player, minqlx.AbstractDummyPlayer)
        if not self.get_cvar("qlx_serversShowInChat", bool) and not irc:
            self.get_servers(servers_list, minqlx.TellChannel(player))
            return minqlx.RET_STOP_ALL

        self.last_time = time.time()
        self.get_servers(servers_list, channel, irc=irc)

    @minqlx.thread
    def get_servers(self, servers_list, channel, irc=False):
        results = []
        for addr in servers_list:
            try:
                info = self.query_server(addr)
                players = self.query_players(addr)
                info["player_list"] = players
                info["players"] = len(players)
                results.append(info)
            except Exception as e:
                results.append({
                    "server": addr,
                    "name": f"Error: {e}",
                    "map": "-",
                    "players": 0,
                    "max_players": 0,
                    "gamestate": "-",
                    "player_list": []
                })
        output = ["\n"]
        output.append("^3{:24}|{:38}|{:12}|{:11}|{}".format(
            "Server Address", "Server Name", "Map", "Game State", "Players"
        ))

        for s in results:
            if s["max_players"] == 0:
                players = "-"
            elif s["players"] >= s["max_players"]:
                players = f"^3{s['players']}/{s['max_players']}"
            else:
                players = f"^2{s['players']}/{s['max_players']}"

            output.append(
                "{:24}|{:38}|{:12}|{:11}|{}".format(
                    s["server"], s["name"][:38], s["map"][:12], s.get("gamestate", "-")[:11], players
                )
            )

            # Player list output
            if s.get("player_list"):
                formatted_players = [
                    f"^7{p[0]} ^4(Time: {p[2]:.1f}s, Score: {p[1]})"
                    for p in s["player_list"]
                ]
                output.append("^3Players: " + ", ".join(formatted_players))

        reply_large_output(channel, output)

    def query_server(self, address):
        ip, port = address.split(":")
        port = int(port)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(2.0)

        # Query rules (includes hostname, map, etc.)
        rules = self.query_rules(sock, ip, port)

        server_name = rules.get("sv_hostname", "unknown")
        map_name = rules.get("mapname", "unknown")
        max_players = int(rules.get("sv_maxclients", "0"))
        game_state = rules.get("g_gameState", "-")
        player_count = 0
        sock.close()

        return {
            "server": address,
            "name": server_name,
            "map": map_name,
            "players": player_count,
            "max_players": max_players,
            "gamestate": game_state
        }

    def query_rules(self, sock, server, port):
        packet = b"\xFF\xFF\xFF\xFFV\xFF\xFF\xFF\xFF"
        sock.sendto(packet, (server, port))
        try:
            data, _ = sock.recvfrom(4096)
        except socket.timeout:
            return {}

        if data.startswith(b"\xFF\xFF\xFF\xFFA"):
            challenge = data[5:9]
            sock.sendto(b"\xFF\xFF\xFF\xFFV" + challenge, (server, port))
            try:
                data, _ = sock.recvfrom(4096)
            except socket.timeout:
                return {}

        if not data.startswith(b"\xFF\xFF\xFF\xFFE"):
            return {}

        buf = data[7:]
        rules = {}
        idx = 0
        while idx < len(buf):
            key_end = buf.find(b"\x00", idx)
            if key_end == -1:
                break
            key = buf[idx:key_end].decode(errors="ignore")
            idx = key_end + 1
            value_end = buf.find(b"\x00", idx)
            if value_end == -1:
                break
            value = buf[idx:value_end].decode(errors="ignore")
            idx = value_end + 1
            rules[key] = value
        return rules

    def query_players(self, address):
        ip, port = address.split(":")
        port = int(port)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(2.0)

        packet = b"\xFF\xFF\xFF\xFFU\xFF\xFF\xFF\xFF"
        sock.sendto(packet, (ip, port))

        try:
            data, _ = sock.recvfrom(4096)
        except socket.timeout:
            return []

        if data.startswith(b"\xFF\xFF\xFF\xFFA"):
            challenge = data[5:9]
            sock.sendto(b"\xFF\xFF\xFF\xFFU" + challenge, (ip, port))
            try:
                data, _ = sock.recvfrom(4096)
            except socket.timeout:
                return []

        if not data.startswith(b"\xFF\xFF\xFF\xFFD"):
            return []

        buf = data[5:]
        num_players = buf[0]
        buf = buf[1:]
        players = []
        idx = 0
        for _ in range(num_players):
            if idx >= len(buf):
                break
            idx += 1  # skip player index
            name_end = buf.find(b"\x00", idx)
            if name_end == -1:
                break
            name = buf[idx:name_end].decode(errors="ignore")
            idx = name_end + 1
            if idx + 8 > len(buf):
                score, duration = 0, 0.0
            else:
                score = int.from_bytes(buf[idx:idx+4], "little", signed=True)
                idx += 4
                duration = struct.unpack("<f", buf[idx:idx+4])[0]
                idx += 4
            players.append((name, score, duration))
        sock.close()
        return players


def reply_large_output(channel, output, max_amount=10, delay=0.5):
    for count, line in enumerate(output, start=1):
        if count % max_amount == 0:
            time.sleep(delay)
        channel.reply(line)
