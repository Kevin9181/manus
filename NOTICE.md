# Notices and attribution

This distribution combines and adapts the following projects:

- **TelegramForwarder**, Copyright its upstream authors and contributors, distributed under GNU GPL version 3. The original license is retained in `LICENSE`.
- **telegram-monitor**, Copyright its upstream author(s), distributed under the MIT License. The original license text is retained in `LICENSE-MIT-telegram-monitor`.

The unified app keeps TelegramForwarder's rule engine, database, Bot UI, AI/RSS/push/media processing and deployment base. The Monitor compatibility importer, imported-rule marker, legacy whitelist support, Monitor-style message metadata and command aliases are integration work in this distribution. No source-file copy of telegram-monitor's runtime is started alongside the Forwarder runtime.

Because the combined distribution contains GPL-3.0 code, the distribution as a whole is offered under GPL-3.0; the retained MIT notice continues to apply to any Monitor-derived material where applicable. Upstream repositories: https://github.com/heavrnl/TelegramForwarder and https://github.com/Kevin9181/telegram-monitor.
