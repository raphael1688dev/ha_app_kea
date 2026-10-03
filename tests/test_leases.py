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
