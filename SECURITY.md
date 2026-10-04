# Security policy

## Supported versions

Only the latest release receives fixes. Please update to it before reporting something.

## Reporting a vulnerability

**Please do not open a public issue for a security problem.** Use GitHub's private vulnerability reporting instead:
open the **Security** tab of this repository and choose **Report a vulnerability**. Include:

- the app version (the *Info* tab of the app in Home Assistant) and your Home Assistant version,
- what an attacker can do and what they need to be able to do it (network position, access to Home Assistant, ...),
- steps to reproduce, or a short proof of concept.

This is a hobby project maintained on a best-effort basis: expect an answer within about a week, a fix when one is
possible, and a credit in the release notes if you want one. There is no bug bounty.

## What is in scope

- The Web UI and its API (`kea/app/`): the check that only the Home Assistant Supervisor may connect, request
  forgery, injection, cross-site scripting (lease hostnames come from devices on your network and are untrusted),
  and anything that lets a client of the network change the DHCP configuration.
- How the app reads and writes its files (`settings.json` and the lease files in its config folder).
- The packaging: the app runs on the host network with the `NET_ADMIN` and `NET_RAW` capabilities, so anything that
  makes it do more than that is of interest.

## What is not in scope

Problems in the software this app packages belong to the projects that own it. Please report them there:

- ISC Kea: <https://github.com/isc-projects/kea/blob/master/SECURITY.md>
- Home Assistant and its Supervisor: <https://github.com/home-assistant/core/blob/dev/SECURITY.md>

DHCP itself has no authentication: any device on the same network can ask for an address, and a rogue DHCP server on
the network can answer instead of this one. That is a property of the protocol, not of this app.
