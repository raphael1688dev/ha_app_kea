import os
from leases import append_deleted_row, find_latest_row, read_leases

HEADER4 = "address,hwaddr,client_id,valid_lifetime,expire,subnet_id,fqdn_fwd,fqdn_rev,hostname,state,user_context\n"
HEADER6 = ("address,duid,valid_lifetime,expire,subnet_id,pref_lifetime,lease_type,iaid,prefix_len,"
           "fqdn_fwd,fqdn_rev,hostname,hwaddr,state,user_context,hwtype,hwaddr_source\n")
NOW = 1_000_000


def write(tmp_path, text):
    path = tmp_path / "l.csv"
    path.write_text(text)
    return str(path)


def test_missing_file(tmp_path):
    assert read_leases(str(tmp_path / "nope.csv")) == []
    assert find_latest_row(str(tmp_path / "nope.csv"), "1.2.3.4") == (None, None)


def test_last_row_wins_and_expired_skipped(tmp_path):
    path = write(tmp_path, HEADER4 +
                 "192.168.1.101,aa:bb:cc:dd:ee:01,,3600,%d,1,0,0,old,0,\n" % (NOW + 100) +
                 "192.168.1.101,aa:bb:cc:dd:ee:01,,3600,%d,1,0,0,new,0,\n" % (NOW + 200) +
                 "192.168.1.102,aa:bb:cc:dd:ee:02,,3600,%d,1,0,0,gone,0,\n" % (NOW - 5) +
                 "192.168.1.103,aa:bb:cc:dd:ee:03,,3600,%d,1,0,0,decl,1,\n" % (NOW + 100))
    leases = read_leases(path, now=NOW)
    assert [(l["address"], l["hostname"], l["remaining"]) for l in leases] == [
        ("192.168.1.101", "new", 200)]
    assert leases[0]["identifier"] == "aa:bb:cc:dd:ee:01"


def test_zero_lifetime_row_means_deleted(tmp_path):
    path = write(tmp_path, HEADER4 +
                 "192.168.1.101,aa:bb:cc:dd:ee:01,,3600,%d,1,0,0,h,0,\n" % (NOW + 100) +
                 "192.168.1.101,aa:bb:cc:dd:ee:01,,0,%d,1,0,0,h,0,\n" % (NOW + 100))
    assert read_leases(path, now=NOW) == []


def test_sorted_numerically(tmp_path):
    rows = "".join("192.168.1.%d,aa:bb:cc:dd:ee:0%d,,3600,%d,1,0,0,,0,\n" % (n, i, NOW + 50)
                   for i, n in enumerate((200, 9, 100)))
    leases = read_leases(write(tmp_path, HEADER4 + rows), now=NOW)
    assert [l["address"] for l in leases] == ["192.168.1.9", "192.168.1.100", "192.168.1.200"]


def test_dhcp6_uses_duid(tmp_path):
    path = write(tmp_path, HEADER6 +
                 "fd00::1000,00:01:00:01,3600,%d,1,1800,0,1,0,0,0,h6,,0,,,\n" % (NOW + 60))
    lease = read_leases(path, now=NOW)[0]
    assert lease["identifier"] == "00:01:00:01" and lease["hostname"] == "h6"


def test_garbage_rows_do_not_crash(tmp_path):
    path = write(tmp_path, HEADER4 + "192.168.1.5,aa,,x,notanumber,1,0,0,,0,\n,,,,,,,,,,\n")
    assert read_leases(path, now=NOW) == []


def test_append_deleted_row_roundtrip(tmp_path):
    path = write(tmp_path, HEADER4 +
                 "192.168.1.101,aa:bb:cc:dd:ee:01,,3600,%d,1,0,0,keep,0," % (NOW + 100) + "\n" +
                 "192.168.1.102,aa:bb:cc:dd:ee:02,,3600,%d,1,0,0,drop,0," % (NOW + 100))  # no trailing newline
    fieldnames, row = find_latest_row(path, "192.168.1.102")
    assert row["hostname"] == "drop"
    append_deleted_row(path, fieldnames, row, now=NOW)
    assert [l["address"] for l in read_leases(path, now=NOW)] == ["192.168.1.101"]
    _, last = find_latest_row(path, "192.168.1.102")
    assert last["valid_lifetime"] == "0" and last["expire"] == str(NOW) and last["hwaddr"] == row["hwaddr"]
    assert find_latest_row(path, "192.168.1.99") == (None, None)


# ------------------------------------------------ lease file cleanup (LFC) files
from leases import lease_file_chain  # noqa: E402


def write_file(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text)
    return str(path)


def row4(addr, mac, host, expire=NOW + 500):
    return f"{addr},{mac},,3600,{expire},1,0,0,{host},0,0\n"


HEADER4_POOL = "address,hwaddr,client_id,valid_lifetime,expire,subnet_id,fqdn_fwd,fqdn_rev,hostname,state,user_context,pool_id\n"


def test_chain_follows_the_order_kea_loads_the_files(tmp_path):
    main = str(tmp_path / "l.csv")
    assert lease_file_chain(main) == []
    for name in ("l.csv", "l.csv.1", "l.csv.2"):
        (tmp_path / name).write_text("address\n")
    assert [os.path.basename(p) for p in lease_file_chain(main)] == ["l.csv.2", "l.csv.1", "l.csv"]
    (tmp_path / "l.csv.completed").write_text("address\n")           # a finished cleanup replaces .2 and .1
    assert [os.path.basename(p) for p in lease_file_chain(main)] == ["l.csv.completed", "l.csv"]


def test_leases_that_were_moved_aside_by_lfc_are_still_listed(tmp_path):
    """After the first LFC (an hour into the run) the main file only holds changes made since then."""
    main = write_file(tmp_path, "l.csv", HEADER4_POOL + row4("192.168.1.102", "aa:bb:cc:dd:ee:02", "renewed-after-lfc"))
    write_file(tmp_path, "l.csv.1", HEADER4_POOL + row4("192.168.1.101", "aa:bb:cc:dd:ee:01", "before-lfc") +
               row4("192.168.1.102", "aa:bb:cc:dd:ee:02", "stale"))
    assert [(l["address"], l["hostname"]) for l in read_leases(main, now=NOW)] == [
        ("192.168.1.101", "before-lfc"), ("192.168.1.102", "renewed-after-lfc")]      # newest file wins


def test_older_files_are_replayed_oldest_first(tmp_path):
    main = write_file(tmp_path, "l.csv", HEADER4_POOL)
    write_file(tmp_path, "l.csv.2", HEADER4_POOL + row4("192.168.1.101", "aa:bb:cc:dd:ee:01", "oldest"))
    write_file(tmp_path, "l.csv.1", HEADER4_POOL + row4("192.168.1.101", "aa:bb:cc:dd:ee:01", "newer"))
    assert read_leases(main, now=NOW)[0]["hostname"] == "newer"


def test_completed_replaces_the_numbered_files(tmp_path):
    main = write_file(tmp_path, "l.csv", HEADER4_POOL)
    write_file(tmp_path, "l.csv.completed", HEADER4_POOL + row4("192.168.1.101", "aa:bb:cc:dd:ee:01", "cleaned"))
    write_file(tmp_path, "l.csv.2", HEADER4_POOL + row4("192.168.1.150", "aa:bb:cc:dd:ee:09", "ignored"))
    assert [l["hostname"] for l in read_leases(main, now=NOW)] == ["cleaned"]


def test_a_deletion_in_the_main_file_hides_a_lease_from_an_older_file(tmp_path):
    main = write_file(tmp_path, "l.csv", HEADER4_POOL)
    write_file(tmp_path, "l.csv.1", HEADER4_POOL + row4("192.168.1.101", "aa:bb:cc:dd:ee:01", "gone"))
    fieldnames, row = find_latest_row(main, "192.168.1.101")
    assert row["hostname"] == "gone" and fieldnames == HEADER4_POOL.strip().split(",")   # header of the MAIN file
    append_deleted_row(main, fieldnames, row, now=NOW)
    assert read_leases(main, now=NOW) == []
    assert find_latest_row(main, "192.168.1.101")[1]["valid_lifetime"] == "0"


def test_the_main_file_may_be_missing(tmp_path):
    main = str(tmp_path / "l.csv")
    write_file(tmp_path, "l.csv.1", HEADER4_POOL + row4("192.168.1.101", "aa:bb:cc:dd:ee:01", "only-old"))
    assert [l["hostname"] for l in read_leases(main, now=NOW)] == ["only-old"]


# ---------------------------------------------------------------- DHCPv6 specifics
def row6(addr, duid, lease_type, prefix_len, hwaddr="", expire=NOW + 500):
    return f"{addr},{duid},3600,{expire},1,1800,{lease_type},1,{prefix_len},0,0,h6,{hwaddr},0,,,\n"


def test_dhcp6_identifier_is_the_duid_even_when_a_mac_is_recorded(tmp_path):
    path = write_file(tmp_path, "l6.csv", HEADER6 + row6("fd00::1000", "00:01:00:01:aa:bb:cc:dd", 0, 0, "aa:bb:cc:dd:ee:ff"))
    lease = read_leases(path, now=NOW)[0]
    assert lease["identifier"] == "00:01:00:01:aa:bb:cc:dd"            # not the MAC: Reserve would build a bogus DUID
    assert lease["kind"] == "address" and lease["prefix_len"] is None


def test_delegated_prefixes_and_temporary_addresses_are_marked(tmp_path):
    path = write_file(tmp_path, "l6.csv", HEADER6 + row6("fd00::1000", "00:01:00:01", 0, 0) +
                      row6("2001:db8:1:100::", "00:01:00:02", 2, 64) + row6("fd00::2000", "00:01:00:03", 1, 0))
    kinds = {l["address"]: (l["kind"], l["prefix_len"]) for l in read_leases(path, now=NOW)}
    assert kinds == {"fd00::1000": ("address", None), "2001:db8:1:100::": ("prefix", 64),
                     "fd00::2000": ("temporary", None)}


def test_dhcp4_leases_are_plain_addresses(tmp_path):
    path = write_file(tmp_path, "l.csv", HEADER4_POOL + row4("192.168.1.101", "aa:bb:cc:dd:ee:01", "h"))
    lease = read_leases(path, now=NOW)[0]
    assert lease["kind"] == "address" and lease["prefix_len"] is None and lease["identifier"] == "aa:bb:cc:dd:ee:01"


# ------------------------------------------------ the deleted row is written like Kea writes rows
def test_deleted_row_is_not_csv_quoted(tmp_path):
    """Kea does not understand CSV quoting; a user_context holding quotes must be written back as it was."""
    context = '{"foo": "bar"}'.replace(",", "&#x2c")
    row_text = f"192.168.1.101,aa:bb:cc:dd:ee:01,,3600,{NOW + 500},1,0,0,h,0,{context},0\n"
    path = write_file(tmp_path, "l.csv", HEADER4_POOL + row_text)
    fieldnames, row = find_latest_row(path, "192.168.1.101")
    assert row["user_context"] == context
    append_deleted_row(path, fieldnames, row, now=NOW)
    last = open(path).read().splitlines()[-1]
    assert last == f"192.168.1.101,aa:bb:cc:dd:ee:01,,0,{NOW},1,0,0,h,0,{context},0"
    assert '""' not in last and '"{' not in last                     # no quoting or doubled quotes


def test_random_mac_and_reservable_hostname_flags(tmp_path):
    main = tmp_path / "kea-leases4.csv"
    main.write_text(HEADER4
                    + f"192.168.1.10,aa:bb:cc:dd:ee:01,,86400,{NOW + 100},1,0,0,laptop_1.,0,\n"          # 0xaa: private
                    + f"192.168.1.11,b4:bb:cc:dd:ee:02,,86400,{NOW + 100},1,0,0,bad name!,0,\n"          # 0xb4: global
                    + f"192.168.1.12,02:42:ac:11:00:02,,86400,{NOW + 100},1,0,0,,0,\n")                   # docker style
    by = {l["address"]: l for l in read_leases(str(main), now=NOW)}
    assert (by["192.168.1.10"]["random_mac"], by["192.168.1.10"]["reserve_hostname"]) == (True, "laptop_1")
    assert (by["192.168.1.11"]["random_mac"], by["192.168.1.11"]["reserve_hostname"]) == (False, "")
    assert by["192.168.1.12"]["random_mac"] is True and by["192.168.1.12"]["reserve_hostname"] == ""


def test_ipv6_leases_have_no_random_mac_flag(tmp_path):
    path = write(tmp_path, HEADER6 + "fd00::1000,00:01:00:01,3600,%d,1,1800,0,1,0,0,0,h6,,0,,,\n" % (NOW + 60))
    lease = read_leases(path, now=NOW)[0]
    assert lease["random_mac"] is None and lease["reserve_hostname"] == "h6"
