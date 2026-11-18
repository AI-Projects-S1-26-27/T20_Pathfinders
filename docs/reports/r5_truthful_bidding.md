# Appendix A: Truthful Bidding Proof Sketch (R5 Vickrey Auction)

## 1. The Rule
In `run_vickrey_auction` (`src/coordination/negotiation.py`), each conflicting vehicle computes its bid as $\text{bid} = \text{priority weight} \times \text{extra seconds}$ of detour delay.  
The highest bidder wins right-of-way on the contested road segment, and all losing vehicles take the detour.  
The winner pays the second-highest bid in virtual credits, while losers pay $0$.

## 2. Worked Example
Consider two vehicles with initial credit balances of $100$ each:
- **Vehicle A:** priority weight $2$, detour cost $30\text{ s}$ $\rightarrow$ true delay value $v_A = 2 \times 30 = 60$.
- **Vehicle B:** priority weight $1$, detour cost $45\text{ s}$ $\rightarrow$ true delay value $v_B = 1 \times 45 = 45$.

When both bid truthfully ($b_A = 60$, $b_B = 45$), **Vehicle A wins** and pays B's bid of **$45$** virtual credits, earning a net surplus of $60 - 45 = +15$.

| A's bid | Who wins | A pays | A's net gain (value $60$) | Comparison vs. truth |
| :--- | :--- | :--- | :--- | :--- |
| **60 (truth)** | A | 45 | $+15$ | Baseline optimal outcome |
| **40 (understate)** | B | 0 | $0$ | Loses a win worth $+15$ |
| **100 (overstate)** | A | 45 | $+15$ | Same winner and same price |

Overstating does not help A because the clearing price ($45$) is set by B's bid, not A's. Understating to $40$ causes A to lose right-of-way it values at $60$ when the price would have been $45$, giving up a surplus of $15$. The same holds for B: if B overbids $80$ to beat A's $60$, B wins but pays $60$ for right-of-way worth only $45$ to B, a net loss of $15$.

## 3. The Sketch
1. **Price independence:** What you pay if you win depends only on the second-highest bid (the highest competing bid), never on your own bid.
2. **Threshold winning:** You win when your bid is above the highest competing bid; at equality, the code's vehicle-ID tie-break decides. Winning gives positive net gain when your true value exceeds the price, zero when equal, and negative gain when lower.
3. **Optimality of truth:** With a single item, independent values, and no binding credit constraints, bidding your true value wins whenever winning is beneficial and avoids winning when the price exceeds your value. A misreport cannot produce a strictly better result; truth-telling is weakly dominant.

## 4. Where It Does Not Hold
This single-shot second-price argument relies on idealized assumptions that our operational mechanism in `run_vickrey_auction` weakens:
- **Credit limits and "no-winner" fallback:** Any vehicle with `credits > 0` bids its full `calculate_delay_cost(r)` (bids are not capped at the vehicle's balance), whereas a vehicle with `0` credits has its bid forced to `0.0`. If the highest bidder has fewer credits than the second-highest bid (`winner_budget.credits < second_highest_bid`), `run_vickrey_auction` returns `(None, losers, 0.0)` rather than awarding the auction to the next bidder. The resolver then applies its separate unpaid-auction fallback (by default, the lowest vehicle ID proceeds and the others yield).
- **Repeated auctions across a shift:** Virtual credits carry over between conflict events (`winner_budget.credits -= second_highest_bid`), so winning now reduces a vehicle's ability to afford future conflicts, creating dynamic opportunity costs not captured by a single-shot proof.
- **Algorithmic rather than strategic bidding:** Vehicles do not freely choose strategic bids; the coordination code computes bids deterministically from priority weights and detour delay (`calculate_delay_cost`). The proof shows that the payment rule makes truthful delay reporting optimal by design, not that agents were simulated behaving strategically.
- **Sequential resolution for 3+ vehicles:** Multi-vehicle conflicts are resolved sequentially rather than via a single combinatorial VCG auction, and this single-item sketch covers a single auction round rather than the multi-step sequence.
- **Winner/loser actions in the simulator:** `run_vickrey_auction` returns a winner and loser IDs; it does not itself execute detours. The sequential resolver currently yields losers by shifting their entire route's `start_time`, rather than inserting a detour or a localized wait.
