@draft @layer:logic @surface:api @surface:ui
Feature: Users and roles
  The panel is shared. Every action is taken by a named user holding one or more roles,
  and each role is a short list of what it may do. A user may hold several roles, but
  one rule never bends: nobody approves their own proposal.

  Background:
    Given these users and roles:
      | user   | roles            |
      | pat    | pm               |
      | alex   | analyst          |
      | quinn  | quant            |
      | rae    | risk             |
      | olly   | operator         |
      | morgan | analyst, quant   |

  Scenario: Each role has a fixed set of permissions
    Then the roles permit exactly:
      | role     | may                                                                      |
      | pm       | read the panel, ask questions, acknowledge, assign and close alerts     |
      | analyst  | everything a pm may, decide review items, override values with a reason |
      | quant    | read the panel, run refreshes and backfills, export, propose settings   |
      | risk     | read everything including the audit log, approve or reject settings     |
      | operator | run refreshes and backfills, see run history, manage users and roles    |

  Scenario: An action outside a user's roles is refused and recorded
    When "pat" tries to decide a review item for loan "2"
    Then the action is refused with "pm may not decide review items"
    And the refusal is written to the audit log with the user and the time

  Scenario: A user with several roles acts under one of them
    When "morgan" proposes the DSCR alert threshold be changed to 1.05x
    Then the proposal records "morgan" acting as "quant"

  Scenario: Nobody approves their own proposal
    Given "morgan" also holds the role "risk"
    And "morgan" proposed the DSCR alert threshold be changed to 1.05x
    When "morgan" tries to approve that proposal
    Then the approval is refused with "a proposal cannot be approved by its proposer"

  Scenario: Role changes are themselves audited
    When "olly" adds the role "risk" to "quinn"
    Then "quinn" holds the roles "quant, risk"
    And the audit log records that "olly" granted "risk" to "quinn" and when
