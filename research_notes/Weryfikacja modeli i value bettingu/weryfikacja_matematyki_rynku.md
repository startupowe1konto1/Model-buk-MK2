# Checking Model Buk's bookmaker maths, market and betting-economics claims against primary sources (as of 2026-09-28, Polish jurisdiction)

Scope: I read the project claims directly from disk (notes 06, 07, 12, 14, 18, 19, 21, 22, Audyt 23-33, `HANDOFF.md`, `backtest/devig.py`, `backtest/clv.py`, `backtest/gate.py`, `reports/devig-calibration.md`, `reports/baseline-null.md`). Then I checked them against external sources. Verdict scale: **correct / partially correct / incorrect / outdated / unverifiable**. The inline tag "(project: ...)" marks where a claim lives in the repo. It is not evidence for the claim.

I re-did every piece of arithmetic in the notes by hand. Only one number is wrong (note 27, see Q5).

---

## Q1. Are the de-vig methods described and implemented correctly, and what does the comparative literature say?

### Takeaway
The three de-vig methods in `backtest/devig.py` (proportional, power, Shin) are implemented correctly. The comments about the favourite-longshot bias are also correct. The literature ranks FLB-aware methods (power, Shin, odds-ratio, Buchdahl's margin-proportional-to-odds) above plain normalisation, and they come out close to each other. The project's own data agree: power, Shin and proportional differ by less than 0.0003 log-loss. Two real problems remain. The method was chosen on the full sample, test seasons included (the project already admits this). And the choice of method, which barely matters for accuracy, changes the project's CLV numbers by roughly as much as the effect being measured (see Q2).

### Cited Findings

#### Verdicts on project claims
| # | Project claim (location) | Verdict | Basis |
|---|---|---|---|
| 1.1 | Proportional de-vig `p_i = (1/o_i)/Σ(1/o_j)` is "a benchmark, not the only correct method" (project: note 06) | **Correct** | Štrumbelj finds Shin probabilities more accurate than basic normalisation for every bookmaker/sport pair — [Štrumbelj 2014, IJF 30(4):934-943](https://www.sciencedirect.com/science/article/abs/pii/S0169207014000533). The `implied` package vignette says the basic method "tend[s] to be the least accurate" — [implied vignette](https://cran.r-project.org/web/packages/implied/vignettes/introduction.html) |
| 1.2 | Proportional "zawyża długie strzały" (overstates longshots) (project: `devig.py` docstring) | **Correct** | Buchdahl: bookmakers "apply differential margin weights... with greater weights the longer the odds". The equal-margin model "fails to apply a large enough margin to longer prices" — [Buchdahl, Wisdom of the Crowd (updated 2017)](https://www.football-data.co.uk/The_Wisdom_of_the_Crowd_updated.pdf). Normalisation "does not account for favorite long-shot bias" — [Clarke, Kovalchik & Ingram 2017](https://www.sciencepublishinggroup.com/article/10.11648/j.ajss.20170506.12) |
| 1.3 | Power: find `k` with `Σ(1/o_i)^k = 1`, which takes more margin off longshots (project: `devig.py`) | **Correct**, both formula and bisection (the sum decreases in `k` because every `q_i < 1`) | The same method is called "power" in `implied`. Buchdahl calls it the "logarithmic function" model: a common exponent maps true to bookmaker probabilities, and "the bigger the 'true' odds the more they will be shortened" — [implied vignette](https://cran.r-project.org/web/packages/implied/vignettes/introduction.html); [Buchdahl WoC](https://www.football-data.co.uk/The_Wisdom_of_the_Crowd_updated.pdf). So the "logarithmic" method in the brief is the project's `power`, not a missing method |
| 1.4 | Shin: `p_i = [sqrt(z² + 4(1−z)q_i²/Σq) − z] / [2(1−z)]`, with `z` = insider share of turnover; larger `z` takes more margin off longshots (project: `devig.py`) | **Correct** | This is the standard closed form of Shin's model used in the comparative literature — [Štrumbelj 2014](https://www.sciencedirect.com/science/article/abs/pii/S0169207014000533). Shin's `z` "estimate[s] the proportion of inside traders" — [implied vignette](https://cran.r-project.org/web/packages/implied/vignettes/introduction.html). Original papers: Shin, H.S. (1991) "Optimal betting odds against insider traders", *Economic Journal* 101(408):1179-1185; Shin (1992) "Prices of state contingent claims with insider traders, and the favourite-longshot bias", *EJ* 102(411):426-435; Shin (1993) "Measuring the incidence of insider trading in a market for state-contingent claims", *EJ* 103(420):1141-1153. The code's limit case at z→0, `q_i/√Σq` (sum = √Σq > 1), is consistent with the formula |
| 1.5 | "Metoda: power, najniższy log-loss na zamknięciu" (project: `reports/devig-calibration.md`) | **Correct but immaterial, and chosen with leakage** | Close log-loss: power 0.99874, Shin 0.99879, proportional 0.99901. Open: 1.00191 / 1.00196 / 1.00219 (project: `reports/devig-calibration.md`). This matches the literature: power ≥ Shin > multiplicative — [Clarke et al. 2017](https://www.sciencepublishinggroup.com/article/10.11648/j.ajss.20170506.12). Buchdahl found it "not clear which model... offers the best means" among the three FLB-aware models — [Buchdahl WoC](https://www.football-data.co.uk/The_Wisdom_of_the_Crowd_updated.pdf). The project's self-criticism that the method was chosen on all seasons including test seasons (project: `HANDOFF.md` §E.3) is **correct** |
| 1.6 | Note 24 lists the M0 engine as "proporcjonalna, Shin, log-odds". The code has proportional, power, Shin (project: Audyt 24 vs `devig.py`) | **Partially correct / incomplete** | Missing FLB-aware methods that did as well in Buchdahl's football tests: odds-ratio (Cheung 2015) and margin-weights-proportional-to-odds (MPTO) — [Buchdahl WoC](https://www.football-data.co.uk/The_Wisdom_of_the_Crowd_updated.pdf); [implied vignette](https://cran.r-project.org/web/packages/implied/vignettes/introduction.html). The additive method is rightly left out: it "can produce negative probabilities, especially for outcomes with low probabilities" — [implied vignette](https://cran.r-project.org/web/packages/implied/vignettes/introduction.html); [Clarke et al. 2017](https://www.sciencepublishinggroup.com/article/10.11648/j.ajss.20170506.12) |
| 1.7 | Note 21 pilot de-vig values (e.g. 2.00/3.50/3.70 → 47.3/27.1/25.6%; 1.40/5.10/8.20 → 69.2/19.0/11.8%; BTTS 1.93/1.75 → 47.6/52.4%) | **Correct** (all five 1X2 examples and the BTTS example recomputed, proportional method) | Arithmetic check. The formula is from [Štrumbelj 2014](https://www.sciencedirect.com/science/article/abs/pii/S0169207014000533) |

#### What the comparative literature says
- Štrumbelj (2014) compared basic normalisation, regression-based methods and Shin across bookmakers and sports. Shin probabilities beat both alternatives for all bookmaker/sport pairs. Some bookmakers are significantly better sources of probability than others. Betting-exchange odds "are not always the best source, especially in smaller markets" — [Štrumbelj 2014](https://www.sciencedirect.com/science/article/abs/pii/S0169207014000533).
- Clarke, Kovalchik & Ingram (2017), *American Journal of Sports Science* 5(6):45-49, tested three large datasets from three sports. Their findings:
  - the additive method can give negative probabilities;
  - normalisation ignores FLB;
  - normalisation and Shin can give probabilities above 1 when applied in reverse;
  - the power method never leaves [0,1] and allows for FLB;
  - power "universally outperforms the multiplicative method and outperforms or is comparable to the Shin method".
  
  Source: [Clarke et al. 2017](https://www.sciencepublishinggroup.com/article/10.11648/j.ajss.20170506.12).
- Buchdahl (Wisdom of the Crowd, updated 2017, football 1X2, Pinnacle as the reference book) tested four models: equal margin (= proportional), MPTO, odds ratio and logarithmic (= power). Equal margin underperforms. The other three are "a reasonable means of removing the bookmaker's margin". MPTO and logarithmic "yield almost identical time series". His worked example: fair odds 1.5/5/7.5 with an 8% margin give MPTO margin weights of 0.04/0.133/0.200 — [Buchdahl WoC](https://www.football-data.co.uk/The_Wisdom_of_the_Crowd_updated.pdf).
- Buchdahl on FLB: "if you purely bet randomly, you will lose less as a percentage betting on favourites than you will on longshots". This holds across several sports — [Buchdahl WoC](https://www.football-data.co.uk/The_Wisdom_of_the_Crowd_updated.pdf).
- The project's own calibration table (power, closing odds, about 86k matches) still shows FLB left over after power de-vig (project: `reports/devig-calibration.md`):
  - longshots over-predicted: bin 0.05-0.10 predicted 7.82% vs observed 7.00%;
  - favourites under-predicted: bin 0.70-0.75 predicted 72.35% vs observed 74.24%; bin 0.75-0.80 predicted 77.34% vs observed 79.13%.

### Inferences
- The code is mathematically sound. Nothing in `devig.py` needs fixing for correctness.
- For forecast accuracy the method choice is second-order (log-loss differences around 0.0003). For CLV it is first-order. Mean CLV on random legs is +0.0119% under proportional, +0.0769% under Shin and +0.1059% under power (project: `reports/baseline-null.md`). The longshot-band baseline is +0.056% / +0.442% / +0.599% (project: `HANDOFF.md` §E.3). Those gaps are about the same size as the model's measured CLV. The likely cause: the Friday snapshot and the close carry different overrounds (3.44% vs 3.10% on average, project: `reports/devig-calibration.md`), and each method moves longshot probabilities differently as the margin shrinks.
- The leftover FLB after power de-vig (favourites underpriced by about 1.5-2 pp in the 0.70-0.90 bins, longshots overpriced by about 0.7-0.8 pp) suggests two tests: odds-ratio/MPTO, or a fitted calibration of the de-vig mapping. Either should be learned inside the training folds only. This is an inference from the project's own table, not a published finding.

### Gaps
- I read the Shin (1991/1992/1993) papers only through secondary descriptions (Štrumbelj; the `implied` vignette), not the originals.
- I found no published comparison of de-vig methods on **Polish** bookmakers' odds.
- Odds-ratio and MPTO have not been tested on the project's own data, so it is unknown whether they remove the leftover FLB.

---

## Q2. Is closing line value an accepted proxy for skill, and is the project's mean-vs-median treatment sound?

### Takeaway
CLV against Pinnacle's close is an accepted, evidence-backed proxy for expected yield. Buchdahl showed a slope of about 1.00 between the price-to-close ratio and realised yield. Recent evidence (2023/24 onward) suggests Pinnacle's close has become a less reliable "truth". The project's CLV definition (fair open ÷ fair close) is non-standard: it measures whether the market moved toward the pick, not the value of the price actually taken. The claim that mean CLV is misleading and median / % beaten are "the right metrics" is **only partly right**:
- The skew is real.
- But under an efficient close, the **mean** is the unbiased estimator of expected yield.
- The median and beat-rate are biased *against* longshot-heavy strategies.

The project's "no skill" conclusion still holds on the correct test: mean CLV against an odds-matched random baseline.

### Cited Findings

#### Verdicts on project claims
| # | Project claim (location) | Verdict | Basis |
|---|---|---|---|
| 2.1 | CLV rather than P&L is the primary metric, because P&L is dominated by variance over hundreds of bets (project: `clv.py`, Audyt 25 §Faza 2) | **Correct (accepted practice)** | Buchdahl, 87,960 Pinnacle odds pairs over four seasons from 2012/13: the ratio of an earlier price to Pinnacle's close predicts yield, and "the graph slope is almost exactly 1.00". He calls the closing odds "highly efficient" — [Buchdahl 2016, football-data blog](https://www.football-data.co.uk/blog/pinnacle_efficiency.php) |
| 2.2 | CLV defined as `fair_open / fair_close − 1`, margin removed at **both** ends (project: `clv.py`) | **Partially correct / non-standard** | The standard definition is the **odds actually taken** divided by Pinnacle's **fair** (margin-removed) closing odds. "If you beat the closing line by 10%, you should expect to make a profit over turnover of 10%" — [Bet2Invest summary of Buchdahl's method, 2024](https://bet2invest.com/blog/Using-The-Closing-Line-To-Test-Your-Skills). In the 2016 study the margin was not removed, so observed yields came out "a few percent higher than expected" — [Buchdahl 2016](https://www.football-data.co.uk/blog/pinnacle_efficiency.php). The project's version tests information (does the market come to us?), not the value of the bet |
| 2.3 | "Dodatni średni CLV to nie dobry znak… patrz na medianę i odsetek pobitych zamknięć", with random betting giving mean +0.1059% vs median +0.0084% as proof (project: `HANDOFF.md` §C.1) | **Partially correct** | (a) The skew is real. (b) The "proof" depends on the de-vig method: under proportional, random legs give mean **+0.0119%**, median **0.0000%**, beat-rate 49.84%; the +0.1059% appears only under power (project: `reports/baseline-null.md`). (c) Buchdahl's skill test uses the **mean** price/close ratio and its standard deviation against a random-betting expectation. Example: 1,214 bets with mean ratio 1.000 vs an expected 0.969 is about 9 standard deviations — [Bet2Invest/Buchdahl](https://bet2invest.com/blog/Using-The-Closing-Line-To-Test-Your-Skills). So in the source methodology the mean is the primary statistic |
| 2.4 | "CLV ma dolną granicę −100%, ale nie ma górnej" (project: `HANDOFF.md` §C.1) | **Partially correct (minor)** | With `CLV = p_close/p_open − 1` and `p_close ≤ 1`, the upper bound is `1/p_open − 1`. That is finite, but large for longshots (e.g. +900% at p_open = 0.10). Arithmetic from the definition in `clv.py` |
| 2.5 | "Model gorszy od losowania": model median −0.48%, beat-rate 47.72% vs random 50.08% (project: `HANDOFF.md` §B) | **Conclusion plausible, argument weak** | A zero-edge strategy that tilts toward longshots will show median < 0 and beat-rate < 50%, because the CLV distribution is more right-skewed at long odds. On the correct comparison the model still fails: its mean CLV of +0.3288% (power, `pl_standard`) is **below** the power longshot-band random baseline of +0.599% (project: `HANDOFF.md` §B and §E.3). Method: [Buchdahl via Bet2Invest](https://bet2invest.com/blog/Using-The-Closing-Line-To-Test-Your-Skills) |
| 2.6 | "CLV spada z sezonu na sezon" is a data artefact: PS* margin drifted, so seasonal CLV comparisons on PS* are unsafe (project: `HANDOFF.md` §C.3) | **Correct in principle** | football-data.co.uk: "Betting odds for weekend games are collected Friday afternoons, and on Tuesday afternoons for midweek games" — [football-data notes.txt](https://www.football-data.co.uk/notes.txt). So PS* is a pre-closing snapshot, not an opening line. Independent evidence that Pinnacle's reference quality changed recently: Pinho (22 Dec 2025, football-data.co.uk data) found that on 6,806 EV bets from 2023/24 onward, expected ROI was 4.2% but actual ROI 1.9%, and wrote that Pinnacle's closing odds "no longer act as a reliable predictor" in Europe's main leagues — [Pinho, "A view from the Pinnacle"](https://networked.substack.com/p/a-view-from-the-pinnacle). Pinnacle closed its public API on 23 July 2025 (secondary, from search results) — [odds-api.io](https://odds-api.io/blog/pinnacle-api-shutdown-alternatives) |
| 2.7 | "CLV mówi po ok. 100 betach, czy naprawdę bijecie rynek" (project: Audyt 25) | **Partially correct** | CLV does converge much faster than P&L. A secondary summary of Buchdahl says a consistent 5% edge over the close "might only need 50 bets" to show significance — [pinnacleoddsdropper summary](https://www.pinnacleoddsdropper.com/blog/closing-line-value--clv-demystified-by-expert-joseph-buchdahl). That applies to a **large** CLV. The project's effects are about 0.1-0.5%, which need thousands of bets (see Inferences) |

#### Evidence base
- Buchdahl 2016: 87,960 Pinnacle odds pairs, four seasons from 2012/13. Slope of earlier-to-close ratio vs yield ≈ 1.00. "The closing odds are highly efficient" — [football-data blog](https://www.football-data.co.uk/blog/pinnacle_efficiency.php).
- Bet2Invest (2024) summary of Buchdahl:
  - 162,672 soccer odds pairs;
  - average opening/closing ratio 0.969 (−3.1% expected);
  - 35.7% of opening odds beat the fair close;
  - fair close computed with the margin removed while accounting for FLB.
  
  Source: [Bet2Invest](https://bet2invest.com/blog/Using-The-Closing-Line-To-Test-Your-Skills).
- Pinho (Dec 2025), football-data.co.uk data. Pre-closing Pinnacle as truth, 2012/13-2025/26: 31,247 EV bets, expected ROI 3.8%, actual 3.6%. Closing odds 2019/20-2025/26: expected 4.3%, actual 3.5%. From 2023/24: expected 4.2%, actual 1.9%. Possible causes he lists: noise, arbitrage-driven line moves, or a real decline in accuracy — [Pinho](https://networked.substack.com/p/a-view-from-the-pinnacle).
- Kaunitz, Zhong & Kreiner (2017), "Beating the bookies with their own numbers — and how the online sports betting market is rigged": a consensus-closing-odds value strategy was profitable, and the authors' bookmaker accounts were then restricted — [arXiv:1710.02824](https://arxiv.org/abs/1710.02824).

### Inferences
- **Why the mean is the EV statistic.** If the fair close is an unbiased (martingale) forecast, then `E[p_close/p_open] = 1`. So a zero-skill strategy has mean CLV of 0 whatever the skew. Skew moves the median below the mean; it does not make the mean positive. A positive mean on random bets therefore shows bias in the de-vig or snapshot, not a "property of the measure". The project's proportional baseline (mean ≈ 0) supports this reading.
- **The right comparisons:**
  - mean CLV against an **odds-band-matched** random baseline;
  - robustness across de-vig methods;
  - confidence intervals **clustered by match** (the H/D/A legs of one match are mechanically negatively correlated: 213,792 legs from 71,264 matches);
  - the median and % beaten as supporting diagnostics only.
- **For a Polish bettor the value metric is** `EV ≈ τ · o_taken / fair_close_Pinnacle − 1`. The project's fair-to-fair CLV can be positive while this value metric is strongly negative, because it ignores both the Polish book's margin and τ.
- **Sample size (my assumption, not measured).** Suppose the per-bet SD of `p_close/p_open` is about 5-8% for Friday-to-close 1X2 moves. Then detecting a mean of +0.3% at 2 SE takes roughly (2 × 0.06 / 0.003)² ≈ 1,600 independent bets. "About 100 bets" is only enough for CLV of several percent.

### Gaps
- I could not reach Pinnacle's own Betting Resources articles on CLV (pinnacle.com refused connections). The Buchdahl findings above come from football-data.co.uk (primary) and Bet2Invest/pinnacleoddsdropper (secondary).
- The PS* drift numbers (2.46% in 2015/16 to 5.08% in 2024/25) could not be recomputed: the raw football-data CSVs are not in this repo (`hazard_saas/data/raw/` sits outside it, per `HANDOFF.md` §G).
- I found no peer-reviewed paper that formally treats mean vs median CLV. The mean-is-unbiased argument above is a derivation, not a citation.

---

## Q3. Polish legal framework as of 2026: gaming tax, pass-through, PIT on winnings, licensed bookmakers, tax-free promos

### Takeaway
The core tax claims are **correct and current**:
- the 12% gaming tax on the sum of stakes (art. 74 pkt 7);
- operators pass it on by applying a factor of 0.88 to the stake, so τ = 0.88;
- a 10% flat PIT on single wins above 2,280 zł, charged on the whole amount, which creates the "cliff".

A government bill to raise the winnings PIT to 15% from 1 January 2026 was **vetoed on 18 December 2025**, so 10% still applies. The Betclic "Bez Podatku 2.0" terms in note 33 match the official regulamin, except the start date (the regulamin says 21.05.2026, the note says 19.05.2026). Betclic is still the only licensed operator offering tax-free *singles*; the others limit tax-free play to AKO or BetBuilder.

### Cited Findings

#### Verdicts on project claims
| # | Project claim (location) | Verdict | Basis |
|---|---|---|---|
| 3.1 | Polish tax is 12% of the stake (project: notes 14, 21, 22, `gate.py`) | **Correct** | Art. 74 of the Ustawa o grach hazardowych sets 12% for "zakładów wzajemnych innych niż wymienione w pkt 6" (pkt 6 is animal-sports bets at 2.5%). Consolidated text Dz.U. 2025 poz. 595 — [arslege, art. 74](https://arslege.pl/stawka-podatku/k185/a17512/). Tax base: "w zakładach wzajemnych – suma wpłaconych stawek". The taxpayer is the operator — [podatki.gov.pl, updated 15.12.2025](http://www.podatki.gov.pl/pozostale-podatki/gry-hazardowe) |
| 3.2 | Operators pass it on as payout multiplier τ = 0.88: "100 zł po kursie 2.00 zwraca 176 zł" (project: notes 21, 22) | **Correct** | Operators "przerzucili ten obowiązek de facto na samych graczy". "Stawiając 100 PLN, tak naprawdę realna stawka wynosi 88 PLN" — [meczyki.pl, 28.01.2026](https://www.meczyki.pl/bukmacherzy/podatek-od-zakladow-bukmacherskich-jaki-jest-podatek-od-zakladow/560). 100 × 0.88 × 2.00 = 176 |
| 3.3 | 10% PIT on a single win above 2,280 zł, on the whole amount; a 2,400 zł win nets less than 2,280 zł (project: note 21, Audyt 23) | **Correct and current** | 10% of the whole win is withheld above the threshold (e.g. a 3,000 PLN win → 300 PLN) — [meczyki.pl](https://www.meczyki.pl/bukmacherzy/podatek-od-zakladow-bukmacherskich-jaki-jest-podatek-od-zakladow/560); legal basis given as art. 30 ust. 1 pkt 2 of the PIT act — [goal.pl (search summary)](https://www.goal.pl/poradnik-typera/podatek-u-bukmachera/). The rise from 10% to 15% (part of the public-health/PIT amendment, planned for 1.1.2026) was vetoed by President Nawrocki on **18.12.2025** — [wnp.pl](https://www.wnp.pl/rynki/podwyzek-tych-podatkow-nie-bedzie-prezydent-nawrocki-zawetowal-kolejne-ustawy,1015549.html); [e-play.pl](https://e-play.pl/prezydent-karol-nawrocki-zawetowal-podwyzke-podatku-od-wygranych/). Cliff: 2,400 × 0.9 = 2,160 < 2,280 |
| 3.4 | Betclic "Gra bez podatku" is unconditional up to 1,000 PLN of total stakes; above that, singles are tax-free only if ≥50% of **stake value** goes on AKO (≥2 events, each ≥1.20) over the last 12 months / 5,000 bets; otherwise τ = 0.94; systems, virtuals and simulated card games excluded (project: Audyt 33 §1) | **Correct** (one detail off) | Official regulamin, fetched 2026-09-28 — [betclic.pl/scp-bezpodatku](https://www.betclic.pl/scp-bezpodatku):<br>• 1,000 PLN total-stakes threshold (§3.3)<br>• qualifying AKO: "co najmniej dwóch zdarzeń… o kursie równym lub wyższym 1.20 każdy" (§3.9)<br>• share = "procentowego udziału stawek postawionych na kwalifikujące się Zakłady wielokrotne w łącznej wartości wszystkich stawek" (§3.5)<br>• window: last 5,000 bets / last 12 months (§3.8)<br>• otherwise "Wskaźnikiem 0.94 (6%)" (§3.11.1)<br>• exclusions: systems, virtual sports, simulated card games (§3.2.1)<br>**Discrepancy:** the regulamin says "Akcja Specjalna trwa od dnia 21.05.2026 r."; note 33 says 19.05.2026, and so does [jakdorobic.pl](https://jakdorobic.pl/gra-bez-podatku-w-betclic/). jakdorobic also says the 50% is counted by coupon number; the regulamin contradicts that, so the project's stake-value reading is right |
| 3.5 | Betclic is the only licensed operator with tax-free **singles**; Fortuna, Superbet, forBET, LVBET, Betfan and Betters offer it only on AKO or BetBuilder (project: note 21) | **Correct** (as of 1.09.2026) | [meczyki.pl, 1.09.2026](https://www.meczyki.pl/bukmacherzy/legalny-bukmacher-bez-podatku-najlepszy-polski-bukmacher/71):<br>• Betclic: SOLO + AKO<br>• forBET: AKO ≥3 events each ≥1.30, first 60 days after registration<br>• LVBET: AKO ≥3 events, total odds ≥2.40, 45 days<br>• Superbet: AKO ≥8 events each ≥1.35 (framed as a 15% boost)<br>• Fortuna: BetBuilder (no minimum listed) and AKO ≥5 events at 1.35<br>• Betters: AKO ≥5 at 1.25<br>• Betfan: AKO ≥5 at 1.40<br>[sportowefakty.wp.pl, 28.05.2026](https://sportowefakty.wp.pl/bukmacherzy/1253201/betclic-zmienia-oferte-dla-graczy-gra-bez-podatku-20-i-wyzsze-wygrane) confirms "maksymalnie 6%" for Betclic after the first 1,000 zł |
| 3.6 | Only Superbet (of PL-licensed books) is on the API-Football list; Pinnacle / 1xBet / Interwetten are not legally available in PL (project: Audyt 28, 32) | **Correct** | As of 28.09.2026, **17 entities / 18 domains** hold MF permits: Fortuna, STS, Superbet, Betclic, LV BET, Totalbet, BetFan, eToto, Betcris, forBET, ComeOn, PZBuk, Fuksiarz, Admiral Bet, Le Bull, Betters, Traf, Wett Arena. Pinnacle is listed among grey-zone operators — [legalsport.pl, citing the MF register](https://www.legalsport.pl/vademecum/poradnik-gracza/lista-legalnych-bukmacherow/). The MF also keeps a register of domains used for illegal gambling (hazard.mf.gov.pl) — [puesc.gov.pl](https://puesc.gov.pl/uslugi/sprawdz-legalnosc-gier-hazardowych-oferowanych-w-internecie) |

#### Legal-framework background
- Online gambling is a state monopoly except zakłady wzajemne and promotional lotteries. Online betting needs an MF permit, granted for 6 years (search-result summaries of MF and biznes.gov.pl pages) — [mf.gov.pl](https://www.mf.gov.pl/web/wp/inne-podatki/podatek-od-gier/gry-hazardowe-przez-internet); [biznes.gov.pl](https://www.biznes.gov.pl/pl/portal/ou1096).
- The MF argued the 10% winnings rate had been unchanged since 2001; the Council of Ministers adopted the 15% bill before the veto — [e-play.pl](https://e-play.pl/rzad-przyjal-projekt-podwyzszajacy-podatek-od-wygranych-w-grach-hazardowych-do-15/); [infor.pl](https://ksiegowosc.infor.pl/wiadomosci/7032504,od-2026-roku-o-50-wzrosnie-stawka-podatku-od-wygranych-i-nagrod-np-w-lotto-konkursach-grach-sprzedazy-premiowej-z-10-do-15-projekt.html).

### Inferences
- The PIT cliff is real but **missing from `gate.py`**. When the payout `τ·o·S` exceeds 2,280 zł, the effective multiplier becomes `0.9·τ` (0.792 at τ = 0.88), and the break-even `p·o` jumps from 1.136 to 1.263. At odds 2.00 this starts at stakes above about 1,295 zł (τ = 0.88) or about 1,140 zł (τ = 1.00). A simple guard would cap the stake at `S < 2280/(τ·o)` or add a PIT-aware τ.
- The 15% winnings-tax proposal was vetoed, not withdrawn. It could come back. It is a regime risk to watch; it is not in force as of 2026-09-28.
- Fortuna's tax-free BetBuilder (no minimum, per meczyki) is technically a way to bet on a single match without the 12% tax. Whether it is "useless" (note 21) depends on BetBuilder margins, which were not measured.

### Gaps
- ISAP (isap.sejm.gov.pl) served a CAPTCHA and the current gov.pl permit register was not reachable. The article text comes from arslege.pl (Dz.U. 2025 poz. 595) and the operator list from legalsport.pl (which cites the MF register). Both should be checked against ISAP and the gov.pl register by hand.
- STS's tax-free offer, if any, is not covered by the meczyki comparison. Not verified.
- The legal definition of "wygrana" for PIT (gross payout incl. stake vs net) comes from secondary sources and operator practice (whole payout taxed). No tax-authority interpretation was retrieved.

---

## Q4. Are the Kelly / fractional Kelly / bankroll treatment and the EV_net threshold logic correct?

### Takeaway
- **EV_net arithmetic:** correct throughout, and all numbers re-verified.
- **Kelly formula in note 06:** textbook-correct but **outdated**, because it ignores τ. With tax, `b = τ·o − 1`. Using the untaxed formula can give a large stake on a bet that is negative after tax.
- **Fractional Kelly / exposure limits:** the stance is sound and backed by the literature on Kelly under parameter uncertainty.
- **EV_net ≥ 5% gate:** arithmetically correct but only **partly sound**. It is a flat threshold on a point estimate with no uncertainty haircut, which systematically admits noise at long odds. That is the optimizer's curse the project measured in note 32. The gate also ignores the PIT cliff and stake limits.

### Cited Findings

#### Verdicts on project claims
| # | Project claim (location) | Verdict | Basis |
|---|---|---|---|
| 4.1 | Full Kelly `f* = (bp − q)/b`, `b = o − 1` (project: note 06) | **Correct as textbook, outdated for PL** | Kelly, J.L. (1956) "A New Interpretation of Information Rate", *Bell System Technical Journal* 35(4):917-926. Under the 12% stake tax the net win per unit stake is `τ·o − 1` (see 3.2, [meczyki.pl](https://www.meczyki.pl/bukmacherzy/podatek-od-zakladow-bukmacherskich-jaki-jest-podatek-od-zakladow/560)). So `f* = (p·τ·o − 1)/(τ·o − 1) = EV_net/(τ·o − 1)`. Example, MB-0002 (p = 0.70, o = 1.61): untaxed Kelly gives **20.8%** of bankroll; taxed Kelly gives **0** (EV_net −0.82%) |
| 4.2 | "W praktyce kandydatem jest fractional Kelly oraz limity ekspozycji. Najpierw musi istnieć stabilna kalibracja poza próbą" (project: note 06) | **Correct** | Replacing the true win probability with an estimate hurts out-of-sample performance. The bet "should be shrunk in the presence of this parameter uncertainty", and shrunken Kelly beat raw Kelly in simulation and on tennis data — [Baker & McHale 2013, *Decision Analysis* 10(3):189-199](https://pubsonline.informs.org/doi/10.1287/deca.2013.0271) |
| 4.3 | "Cała sekcja Kelly jest fikcją, dopóki limity nie są zamodelowane" (project: Audyt 23 §3) | **Correct in substance** | Successful value bettors get restricted — [Kaunitz et al. 2017, arXiv:1710.02824](https://arxiv.org/abs/1710.02824). Buchdahl notes "the regular restriction and banning of customers who regularly exploit best market loss-leading value" — [Buchdahl WoC](https://www.football-data.co.uk/The_Wisdom_of_the_Crowd_updated.pdf) |
| 4.4 | `EV_gross = p·o − 1`, `EV_net = p·τ·o − 1`; break-even `p·o = 1/τ = 1.13636`; EV_net ≥ 5% needs `p·o ≥ 1.19318` (τ = 0.88) or `1.11702` (τ = 0.94) (project: notes 14, 21, 22, `gate.py`) | **Correct** | Follows from 3.1-3.2. All values recomputed |
| 4.5 | Frozen bets MB-0001..0004 have EV_net −6.59%, −0.82%, −2.84%, −1.26% at τ = 0.88; note 22's example p = 0.60, o = 1.90 gives EV_gross +14% but EV_net +0.32% (project: notes 21, 22, Audyt 23) | **Correct** | Recomputed: 0.55·1.93·0.88 = 0.9341; 0.70·1.61·0.88 = 0.9918; 0.61·1.81·0.88 = 0.9716; 0.66·1.70·0.88 = 0.9874; 0.60·1.90·0.88 = 1.0032 |
| 4.6 | Break-even `p` at odds 1.90 is 59.8% (vs 52.6% untaxed); at 1.30 it is 87.4% (project: Audyt 23) | **Correct** | 1/(1.90·0.88) = 0.598; 1/(1.30·0.88) = 0.874 |
| 4.7 | Decision gate: `edge > edge_min` and `EV_net ≥ EV_min = 0.05` (project: `gate.py`, note 22 §5) | **Partially sound** | Arithmetic is fine. But there is no uncertainty haircut, so selecting on the largest estimated EV inflates realised error. This is "the optimizer's curse": Smith, J.E. & Winkler, R.L. (2006) "The Optimizer's Curse: Skeptical Decision Making in Management Analysis", *Management Science* 52(3):311-322. The project measured exactly this on 2,307 legs: model promised 0.3944, market 0.2388, realised 0.2293 (project: Audyt 32 §3). The ≥ vs > inconsistency between note 22 §5 and `HANDOFF.md` is cosmetic and documented in `gate.py` |

### Inferences
- **Why a flat EV threshold favours noise at long odds.** `SE(EV_net) = τ·o·SE(p)`. With SE(p) = 0.03, the SE is about 4.0% at odds 1.5 but about 10.6% at odds 4.0. A fixed +5% threshold is therefore about 1.25 SE at short odds but only about 0.5 SE at long odds. That matches the project's findings that "ostrzejszy próg przechyla wybór w stronę długich kursów" and that the surplus grows with threshold strictness. Two remedies:
  1. shrink `p_model` toward `p_market` before computing EV (a weight fitted out-of-fold), which works as a principled haircut; or
  2. require `EV_net ≥ k·SE(EV_net)`.
- **What the gate still lacks:** (i) a PIT-aware τ above the 2,280 zł payout; (ii) stake caps / limits; (iii) Kelly on the taxed odds; (iv) handling of simultaneous bets and correlated legs (same-match legs, AKO) if Kelly is ever used.
- The "haircut" in the brief does not exist as an explicit parameter anywhere in the repo. A grep for "haircut" found nothing outside `.venv`. `EV_min` acts as an implicit flat haircut.

### Gaps
- I did not retrieve empirical evidence on fractional-Kelly performance specific to football betting beyond Baker & McHale (tennis plus simulation). An experimental review exists (Uhrín et al., [arXiv:2107.08827](https://arxiv.org/pdf/2107.08827)) but I did not read it.
- No data on the actual stake limits of Polish operators was found.

---

## Q5. Are the margin / overround numbers plausible against independently published figures?

### Takeaway
- **Margin arithmetic:** every figure is correct except one. Note 27 says the 1X2 book on the same STS match "sums to 115.5%"; it actually sums to 103.5%. That turns the "similar margin" remark into a roughly 4× gap.
- **Pinnacle team corners about 6.1%:** plausible but based on two quotes from one match.
- **STS corners-bands 13.7%:** plausible but also a single match.
- **PS* margin drift (2.46% → 5.08%):** could not be verified here, and it partly conflicts with the project's own full-sample average (3.44% PS*, 3.10% close).
- **"Dispersion worth about 8 pp vs tax about 11.5 pp":** arithmetically right but framed too kindly. The best decile already loses 3.95% *before* tax.
- **Note 33's "AKO costs about 2× the tax":** relies on average vig and ignores FLB; with heavy-favourite legs the cost is roughly at parity with the tax.

### Cited Findings

#### Verdicts on project claims
| # | Project claim (location) | Verdict | Basis |
|---|---|---|---|
| 5.1 | Pinnacle team corners margin about 6.1% (Home O/U 5.5 at 1.70/2.10; Away O/U 4.5 at 1.81/1.95) (project: Audyt 28 §5) | **Arithmetic correct; plausible; unverifiable as a market figure** | 1/1.70 + 1/2.10 = 1.0644 → margin 6.05%; 1/1.81 + 1/1.95 = 1.0653 → 6.13%. Pinnacle is usually quoted at about 2% on top-league 1X2 vs an industry average of about 6% (secondary) — [completesports](https://www.completesports.com/pinnacles-low-margin-business-model/). Higher margins on secondary markets are consistent with that. n = 2 quotes from one match, so this is not an estimate of Pinnacle's corners margin |
| 5.2 | STS team corners in bands (0-2 / 3-4 / 5-6 / 7+ at 5.40 / 2.95 / 3.10 / 3.20) → overround 15.93%, margin 13.74% (project: Audyt 27 §1) | **Arithmetic correct; plausible; single match** | 0.1852 + 0.3390 + 0.3226 + 0.3125 = 1.1593; 1 − 1/1.1593 = 13.74%. No independent published STS corners margin was found |
| 5.3 | "Dla porównania 1X2 na tym samym meczu: 2.30 / 3.60 / 3.10 → suma 115.5%... czyli podobnie" (project: Audyt 27 §1) | **Incorrect** | 1/2.30 + 1/3.60 + 1/3.10 = 0.4348 + 0.2778 + 0.3226 = **1.0352** (overround 3.5%). The corners-band margin was about 4× the 1X2 margin on that match, not "similar". Either the quoted 1X2 odds or the sum is wrong |
| 5.4 | PS* average margin rose from 2.46% (2015/16) to 5.08% (2024/25), "na zamknięciu ok. 3.9%" (project: `HANDOFF.md` §C.3) | **Unverifiable here; direction plausible; internally inconsistent** | PS* odds are Friday/Tuesday-afternoon snapshots — [football-data notes.txt](https://www.football-data.co.uk/notes.txt). Pinnacle's data and reference quality changed after 2023 — [Pinho 2025](https://networked.substack.com/p/a-view-from-the-pinnacle); [odds-api.io on the July 2025 API closure](https://odds-api.io/blog/pinnacle-api-shutdown-alternatives). But the project's own full-sample average is **3.44% (PS\*) → 3.10% (close)** (project: `reports/devig-calibration.md`), so "≈3.9% at close" can only describe recent seasons. No per-season table is committed to back it |
| 5.5 | "Cała mierzalna rozbieżność... warta ok. 8 pp yieldu. Polski podatek kosztuje ok. 11,5" (project: Audyt 32 §0, §4-5) | **Partially correct** | Arithmetic: decile 0 at −3.95% vs decile 9 at −12.28% gives 8.33 pp; tax cost at −3.95% is 0.12 × 0.9605 = 11.53 pp; 0.9605 × 0.88 − 1 = −15.47% and 0.9605 × 0.94 − 1 = −9.71% (all match notes 32-33). Framing: the 8.3 pp is a best-vs-worst-decile spread, not an edge over break-even. The best decile is already negative before tax. The sample is Friday snapshots of about 10 mostly non-PL-licensed books (project: Audyt 32 §6), so it does not describe Polish books' dispersion |
| 5.6 | Consensus (average of books) after de-vig is about as sharp as Pinnacle, and sharper in 7 of 22 leagues (project: Audyt 32 §2) | **Plausible / consistent with literature** | Bookmakers differ significantly as probability sources, and the sharpest/exchange odds are "not always the best source, especially in smaller markets" — [Štrumbelj 2014](https://www.sciencedirect.com/science/article/abs/pii/S0169207014000533). Buchdahl, however, singles out Pinnacle as "the stand out bookmaker for assessing 'true' odds" — [Buchdahl WoC](https://www.football-data.co.uk/The_Wisdom_of_the_Crowd_updated.pdf) |
| 5.7 | Buying τ = 1.00 costs about 12.35% (2-leg AKO at average vig: overround 6.81% → 6.38% per leg) vs 6% tax, so "kupowanie zwolnienia kosztuje ok. dwa razy więcej niż sam podatek" (project: Audyt 33 §2) | **Partially correct** | Arithmetic: 1 − 1/1.0681 = 6.38%; 1 − (1/1.0681)² = 12.35%; three legs 17.9% (correct). But it assumes margin is spread evenly. Under FLB, favourites carry much less margin: in Buchdahl's MPTO the margin weight is `M·F/n` (the 8%-book example gives 0.04 on the 1.5 favourite vs 0.200 on the 7.5 longshot) — [Buchdahl WoC](https://www.football-data.co.uk/The_Wisdom_of_the_Crowd_updated.pdf). The regulamin allows legs down to 1.20 — [betclic.pl](https://www.betclic.pl/scp-bezpodatku) |
| 5.8 | Margin ladder: 1X2 close about 3.0% (Big-5), match corners close 5.0%, cards 5.2%, Pinnacle team corners 6.1%, Bet365 7.2-8.5%, 1xBet 7.5-10.1%, STS bands 13.7%. "Marża rośnie z egzotycznością rynku i spada ku kickoffowi" (project: Audyt 29 §E) | **Plausible; unverifiable (single sources)** | The ordering matches the general pattern (main markets and Pinnacle lowest). The project's own PS* → close fall (3.44% → 3.10%) supports "falls toward kickoff" — (project: `reports/devig-calibration.md`) |
| 5.9 | Polish books' 1X2 margins (implicit in "bukmacher bez podatku może mieć kursy o 13,6% gorsze i nadal wygrywać", note 21) | **Correct as arithmetic; empirical margins unverified** | 1/0.88 = 1.136. I found no independently measured, dated 1X2 margins for STS / Superbet / Betclic / Fortuna. A search snippet attributed "Superbet 4.2-4.5%, STS ~4.5%" to legalsport.pl, but the fetched page did not contain these figures — [legalsport.pl](https://www.legalsport.pl/bukmacherzy/superbet/jakie-kursy-ma-superbet-zaklady-bukmacherskie/) |

### Inferences
- **FLB-aware cost of the AKO route (5.7).** Take a 6.8% book, n = 3, and a favourite with fair odds 1.30. Its margin weight is 0.068 × 1.30 / 3 ≈ 0.0295, so the book price is about 1.26 (≥ 1.20 qualifies) and the expected loss about 2.9% per leg. A 2-leg AKO then loses about **5.6%**, roughly the same as strategy A's 6% tax, not double it.
  - The project's own calibration shows favourites at close were, if anything, *under*-priced (0.70-0.80 bins observed 1.8-1.9 pp above predicted), which would lower the AKO cost further.
  - The overall verdict of note 33 (no tax regime makes the measured edge positive) still holds, because even τ = 1.00 gives −3.95% [CI −5.33%, −2.57%].
  - The "2× the tax" sentence should be re-estimated with odds-band-specific vig, using Betclic's own prices.
- **Fix note 27.** The 1X2 comparison is wrong, and the "13.7% vs 6.1%" story should carry an explicit n.
- **Show the PS* drift per season.** Publish the per-season PS* and PSC* overround table (with n per season) so the 2.46% → 5.08% and "≈3.9% close" figures can be checked. Also check whether 2025/26 PS* rows are still genuine Pinnacle prices after the July 2025 API closure.

### Gaps
- Pinnacle's own margin statements (pinnacle.com Betting Resources) could not be fetched (connection refused). The "≈2% on top-league 1X2" figure is secondary.
- There is no independent, dated measurement of Polish licensed bookmakers' 1X2 or corners margins. This should be measured directly: snapshot several PL books over many matches, not one.
- Hegarty & Whelan's working paper "Calculating the Bookmaker's Margin: Why Bets Lose More on Average than You Are Warned" (UCD WP23/04; MPRA 116924) is directly relevant (overround-based margin vs actual expected loss), but both copies were unreachable (403 / connection refused). Only the title was verified — [UCD WP23_04](https://www.ucd.ie/economics/t4media/WP23_04.pdf).

---

## Q6. What important concepts do the notes miss?

### Takeaway
The notes are unusually rigorous on tax arithmetic and anti-leakage discipline. The main gaps are in four places:
1. how CLV is measured: price-taken CLV, odds-matched baselines, clustered CIs, de-vig robustness;
2. how uncertainty enters the decision: shrinkage to market / optimizer's curse, SE-scaled thresholds;
3. how margin is spread across odds (FLB) in cost estimates;
4. operational constraints not in the gate: PIT cliff, stake limits, taxed Kelly, provenance of the Pinnacle data after 2025.

### Cited Findings
- **Price-taken CLV vs fair-to-fair CLV.** The standard skill/value metric compares the odds actually taken with Pinnacle's fair close, and yield ≈ that ratio minus 1 — [Bet2Invest/Buchdahl](https://bet2invest.com/blog/Using-The-Closing-Line-To-Test-Your-Skills); [Buchdahl 2016](https://www.football-data.co.uk/blog/pinnacle_efficiency.php). The project's `clv.py` only measures fair-price drift. The notes never state `EV ≈ τ·o_taken/fair_close − 1`.
- **Unequal margin allocation (FLB) in every cost figure.** Random bettors lose less on favourites than on longshots — [Buchdahl WoC](https://www.football-data.co.uk/The_Wisdom_of_the_Crowd_updated.pdf). Notes 27, 28 and 33 use average vig ("6.38% per leg") or book margin as if it applied to every leg.
- **Optimizer's curse / shrinkage toward the market.** Smith & Winkler (2006), *Management Science* 52(3):311-322. Combining bookmaker odds with historical-data models improves football forecasts — [Egidi, Pauli & Torelli 2018, arXiv:1802.08848](https://arxiv.org/pdf/1802.08848). The notes have `PURE/MARKET/HYBRID` (note 06), but no formal shrinkage of `p_model` toward `p_market` before the EV gate.
- **Kelly under parameter uncertainty and with tax.** Shrink the Kelly fraction when p is estimated — [Baker & McHale 2013](https://pubsonline.informs.org/doi/10.1287/deca.2013.0271). Use `b = τ·o − 1` (Q4).
- **Account restrictions as a hard constraint.** [Kaunitz et al. 2017](https://arxiv.org/abs/1710.02824); [Buchdahl WoC](https://www.football-data.co.uk/The_Wisdom_of_the_Crowd_updated.pdf). Audyt 23 §3 raises this, but it is not in `gate.py` or any backtest.
- **Winnings-PIT cliff in the decision rule.** 10% on the whole payout above 2,280 zł, still in force after the 18.12.2025 veto — [meczyki.pl](https://www.meczyki.pl/bukmacherzy/podatek-od-zakladow-bukmacherskich-jaki-jest-podatek-od-zakladow/560); [wnp.pl](https://www.wnp.pl/rynki/podwyzek-tych-podatkow-nie-bedzie-prezydent-nawrocki-zawetowal-kolejne-ustawy,1015549.html). Flagged "do dopisania" in Audyt 23, still not implemented.
- **Changing reliability of the benchmark.** The Pinnacle close under-delivered its implied expected ROI from 2023/24 (expected 4.2% vs actual 1.9% on 6,806 bets) — [Pinho 2025](https://networked.substack.com/p/a-view-from-the-pinnacle). Pinnacle's public API was closed in July 2025 (secondary) — [odds-api.io](https://odds-api.io/blog/pinnacle-api-shutdown-alternatives). The notes treat Pinnacle close as a fixed "ground truth".
- **Choice of de-vig reference and method-robustness of CLV.** The project's own baselines show mean CLV depends on the de-vig method (+0.012% / +0.077% / +0.106% for random legs; project: `reports/baseline-null.md`). The literature treats method choice as an accuracy question — [Clarke et al. 2017](https://www.sciencepublishinggroup.com/article/10.11648/j.ajss.20170506.12); [Štrumbelj 2014](https://www.sciencedirect.com/science/article/abs/pii/S0169207014000533). The notes do not require CLV conclusions to hold across methods.
- **Missing de-vig variants.** Odds-ratio (Cheung 2015) and MPTO (Buchdahl) performed as well as power in football — [Buchdahl WoC](https://www.football-data.co.uk/The_Wisdom_of_the_Crowd_updated.pdf); [implied vignette](https://cran.r-project.org/web/packages/implied/vignettes/introduction.html).
- **Betclic promo nuances.** The 50% condition is on stake value over a rolling 12-month / 5,000-bet window. AKO legs may be as short as 1.20. Systems are excluded — [betclic.pl regulamin](https://www.betclic.pl/scp-bezpodatku). The notes model a static 50/50 split. In practice the window, and the chance of meeting it with low-vig favourite legs, matter.

### Inferences
- **Statistical-testing hygiene is missing.** The notes do not mention:
  - confidence intervals clustered by match (H/D/A legs of one match are dependent);
  - multiple-testing control across 22 leagues × several markets × three de-vig methods × tax regimes;
  - odds-band-matched random baselines as the default comparator.
  
  These are standard statistical practice and follow from the dependence structure described in Q2. No specific source was retrieved for betting.
- **Promotions and price boosts** at PL-licensed books are a separate possible source of +EV that could beat the 12% tax. The notes do not assess them. I did not research them (out of scope).
- **Two-way, lower-margin markets** (Asian handicap, main O/U lines) appear only as a "hypothesis" in note 14. They are the markets where the tax-to-margin ratio is least bad and where Pinnacle's close is most liquid.

### Gaps
- I found no source measuring whether Polish operators' prices converge to Pinnacle's close (i.e. whether "soft PL price vs sharp consensus" CLV exists on PL books). The project's M0 used mostly non-PL books.
- I found no source on BetBuilder margins at Fortuna (relevant to its unlimited tax-free BetBuilder offer).
