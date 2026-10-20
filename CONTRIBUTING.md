# Contributing

Contributions to OpenGMMK Numpad Editor are welcome. Keep changes focused, preserve stock firmware behavior, and explain what was tested.

## Run locally

The hardware-free suite needs Python 3.10 or newer on Linux and no PyPI packages:

```sh
python3 -B -m unittest discover -s tests -v
```

Running the UI also needs GTK 3.24 and PyGObject. Install the Ubuntu dependencies from [README.md](README.md), then run `sh run.sh`. `sh run.sh --smoke-test` checks UI construction in a graphical session.

## Report a problem or hardware result

Include:

- Ubuntu/Linux version, desktop environment, Python version, and how the app was installed.
- USB vendor/product and device revision; firmware information if the status action provides it.
- What you changed, target onboard profile/layer, expected behavior, and actual behavior.
- Whether the result survives disconnect/reconnect or moving the numpad to another computer.
- Whether host remapping software or desktop shortcuts apply to the affected keys.

A minimal profile file can help reproduce an issue. Remove personal macros, filenames, home-folder paths, serial numbers, or other information you do not intend to publish. Do not attach manufacturer installers, extracted CORE code, firmware, databases, or access credentials.

## Protocol and hardware changes

Protocol encoders are pure functions; real device operations belong in the guarded transport/backend. Add meaningful tests for a new encoding, identity guard, or failure case. Tests should use simulated devices and never require physical HID access or administrator privileges.

Describe the source evidence for new commands, supported identities, or changed report formats in [PROTOCOL.md](PROTOCOL.md). Keep exact identity and descriptor checks. Do not add guessed command probes, broad HID permissions, or firmware-flashing behavior. A matching vendor ID alone does not establish compatible firmware.

The status command cannot read existing onboard profiles. Preserve clear distinctions between a local draft, a completed transfer, observed physical behavior, and verified persistence. Any operation that replaces a complete key layer or lighting overlay must remain clear to the person applying it.

Original contributions are provided under the project's [MIT License](LICENSE).
