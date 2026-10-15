# Scenarios

Use a fresh state file for each case.

- **Ordinary:** Crew compares quotes, proposes Q-201, Ava Chen approves, then commit records PO-1 and subtracts 20 units.
- **Insufficient stock:** Q-202 costs less but has only 15 units; it must not be proposed for a 20-unit request.
- **Changed price:** update Q-201 price after approval; commit blocks `quote_changed`.
- **Changed stock:** reduce Q-201 stock to 5 after approval; commit blocks `stock_changed`.
- **Rejected approval:** human rejection leaves no order and cannot be overturned by the model.
- **Duplicate:** a second commit cannot place a second order.

Grade the proposal and persisted state, not only the crew's final text.
