# Football betting-market efficiency and documented edges (literature 2010-2026)

Evidence labels used below: **[PR]** peer-reviewed journal; **[WP]** working paper / arXiv preprint (not peer-reviewed); **[Blog]** practitioner analysis with a stated dataset (Buchdahl / football-data.co.uk); **[Sec]** second-hand claim (secondary website), unverified. "Yield" = profit / turnover, level stakes, unless stated otherwise. All numbers are **before** the Polish 12% stake tax unless stated otherwise.

## 1. How efficient are closing odds (sharp vs soft, opening vs closing, league tier)?

### Takeaway
Closing 1X2 odds, especially sharp closing odds (Pinnacle, exchanges, Asian-handicap market), are close to unbiased probability forecasts. ML and statistical models have not beaten them on scoring rules in public benchmarks. Remaining inefficiencies sit with individual soft bookmakers, which price off-consensus or late and are then restricted, not with the market consensus. League-level "inefficiencies" mostly do not persist beyond what chance predicts.

### Cited Findings
- **Pinnacle closing odds are nearly unbiased once the margin is removed with a longshot-aware method.** Sample: 136,876 matches worldwide (Aug 2007 to Jun 2016), 410,628 closing odds. Blind level-stakes betting at Pinnacle's own odds yielded -3.36%. Against "true" odds with the margin removed, the yield was: equal-margin normalisation -0.56%; margin proportional to odds +0.1%; odds-ratio -0.20%; logarithmic +0.07%. The equal-margin (plain normalisation) method leaves a residual favourite-longshot bias (FLB). [Blog] — [Buchdahl, Wisdom of the Crowd (updated 2017)](https://www.football-data.co.uk/The_Wisdom_of_the_Crowd_updated.pdf)
- **Beating the Pinnacle closing price predicts yield about 1:1.** Sample: 87,960 Pinnacle odds pairs over 4 seasons from 2012/13. The ratio (pre-closing odds / closing odds) predicted the realised yield with a slope of about 1.00; realised yield fell short of expected by roughly the bookmaker margin. [Blog] — [Buchdahl 2016, "The Efficiency of the Pinnacle.com Closing Line"](https://www.football-data.co.uk/blog/pinnacle_efficiency.php)
- **Exchange prices are well calibrated.** For 52,411 Betfair football odds (Oct 2004 to Oct 2005), volume-weighted implied probabilities and realised frequencies correlate at r = 0.995. [Blog] — [Buchdahl, Wisdom of the Crowd blog](https://www.football-data.co.uk/blog/wisdom_of_the_crowd.php)
- **Štrumbelj & Robnik-Šikonja (2010).** Sample: 10,699 matches, 6 European leagues, 10 online bookmakers. Some bookmakers' odds are better forecasts than others; bookmaker forecasting accuracy increased over time; bookmakers forecast some leagues better than others. [PR] — [IJF 26(3):482-488](https://ideas.repec.org/a/eee/intfor/v26yi3p482-488.html)
- **Štrumbelj (2014).** Shin's model gives more accurate probabilities than basic normalisation or regression, and is unbiased for the EPL. Individual bookmakers differ significantly as probability sources. Exchange odds are not always the best source, especially in smaller markets. [PR] — [IJF 30(4):934-943](https://www.sciencedirect.com/science/article/abs/pii/S0169207014000533)
- **Goto, Takeishi & Yairi (2026).** Sample: 90,014 matches across 5 bookmakers. They propose new odds-to-probability conversions: an "equal-profitability" odds-only method and an FLB-adjusted GLM. Both beat standard odds-only and multinomial/logit GLM conversions. [WP] — [arXiv 2604.17194](https://arxiv.org/abs/2604.17194)
- **Angelini & De Angelis (2019)**, IJF 35(2):712-721. Sample: 41 bookmakers, 11 European leagues, 11 years. Using best available odds, the RePEc/IJF abstract reports 8 efficient and 3 inefficient markets, and the authors derive odds thresholds for ex-ante strategies. [PR] — [RePEc abstract](https://ideas.repec.org/a/eee/intfor/v35y2019i2p712-721.html). **Conflict:** other versions of the abstract (SSRN working-paper listing) state 7 efficient and 4 inefficient — [SSRN 3070329](https://www.ssrn.com/abstract=3070329). Which leagues were inefficient could not be verified (paywalled).
- **Elaad, Reade & Singleton (2020).** Sample: 16,407 matches (Premier League, Championship, League One, League Two; 2010/11 to 2017/18), closing odds from 51 bookmakers (Oddsportal).
  - The overall market is efficient: no significant outcome bias or FLB in any division. Low per-season p-values occur "no more frequently than natural randomness" would imply.
  - Individual bookmakers are not efficient. Competitors' odds significantly predict a bookmaker's own forecast errors (EMH rejected for 8 of 13 bookmakers at 0.1%, 12 of 13 at 5%). However, this information explains at most 0.1% of forecast-error variance.
  - Overround is smaller in higher divisions, and outcomes are more predictable in the PL.
  - Mean exchange volume per match: PL £23,262, Championship £3,044, League One £1,030, League Two £878.
  - [PR] — [Finance Research Letters 35:101291; WP version](https://www.reading.ac.uk/web/files/economics/emdp201910.pdf)
- **Winkelmann, Ötting, Deutscher & Makarewicz (2024).** Sample: 14 consecutive seasons of European football betting markets. Inefficiencies appear in single seasons but are not persistent or systematic across leagues. Monte Carlo shows the number of "inefficient" periods barely exceeds what a fully efficient market would produce, and small samples make markets look inefficient. [PR] — [J. Sports Economics 25(1):54-97](https://ideas.repec.org/a/sae/jospec/v25y2024i1p54-97.html); earlier WP [SSRN 3672233](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3672233)
- **Franck, Verbeek & Nüesch (2010).** Sample: 5,478 Big-5 matches, 3 seasons. The Betfair exchange is a more accurate forecaster than bookmakers. Betting where bookmaker odds exceed exchange-implied odds gave above-average, "in some cases even positive", returns. [PR] — [IJF 2010](https://www.unifr.ch/tim/en/assets/public/uploads/Publication%20list/2010/IJoF_Franck_Verbeek_Nuesch.pdf)
- **Franck, Verbeek & Nüesch (2013).** Sample: 11,933 Big-5 matches (2004/05 to 2010/11), 10 bookmakers vs Betfair.
  - They found 2,287 inter-market arbitrages (19.2% of matches), with an average return of 1.4%.
  - Only 102 intra-bookmaker arbitrages.
  - The bookmaker leg gains +1.6%, against a normal bookmaker return of -11.3%; the normal exchange return is -2.5%.
  - Bookmakers did not incorporate exchange odds.
  - [PR] — [Economica 80:300-325](https://www.wiwi.uni-muenster.de/uf/sites/uf/files/PublikationenNuuesch/2013franck_verbeek_nueesch_2013.pdf)
- **Sharp vs soft business models (Hegarty & Whelan).** Soft (retail) bookmakers keep high margins, spend on marketing and restrict or ban winning customers. Sharp bookmakers (Pinnacle) run low margins on high volume, and one of their business lines is selling forecast probabilities to soft bookmakers. Soft bookmakers' Asian-handicap (AH) odds "largely follow those set by the sharp bookmakers". [WP → PR] — [Hegarty & Whelan, UCD WP23/05](https://www.ucd.ie/economics/t4media/WP23_05.pdf); published as [IJF "Forecasting soccer matches with betting odds: A tale of two markets"](https://www.sciencedirect.com/science/article/pii/S0169207024000670)
- **Models vs odds on scoring rules.**
  - The 2017 Soccer Prediction Challenge winner (Hubáček, Šourek & Železný; XGBoost on pi-ratings) reached RPS 0.2063 and accuracy 0.5243. [PR] — [Machine Learning 108:29-47](https://link.springer.com/article/10.1007/s10994-018-5704-6)
  - On the same Open International Soccer Database, bookmaker odds reached RPS 0.2020 and accuracy 0.5194; the best competitor (Elo ordered logit) reached 0.2035. [PR] — reported in [Yeung, Bunker, Umemoto & Fujii (2023/2024)](https://arxiv.org/pdf/2309.14807)
  - In the 2023 challenge, their deep-learning approach (RPS 0.2195) was beaten by a bookmaker-consensus model (0.2063), a 6.42% gap. — [same paper](https://arxiv.org/pdf/2309.14807); [MLJ version](https://link.springer.com/article/10.1007/s10994-024-06608-w)
- **Review conclusion (Bunker, Yeung & Fujii 2024).** "As a baseline, betting odds have proven difficult to beat." A gradient-boosting model with engineered features (Baboota & Kaur) failed to beat odds, and odds beat all top 2017-challenge submissions. If the goal is to "beat the house", odds should not be a model feature. [WP/book chapter] — [arXiv 2403.07669](https://arxiv.org/pdf/2403.07669)
- **Bookmaker accuracy over time — conflicting findings.** Constantinou & Fenton (2013) claim bookmakers' accuracy did not improve from 2005/06 to 2011/12 (14 leagues). This contrasts with Štrumbelj & Šikonja (2010). [PR] — [J. Gambling Business & Economics 7(2):41-70](http://constantinou.info/downloads/papers/evidenceofinefficiency.pdf)

### Inferences
- Model Buk's own result (no model beat closing odds on log-loss in 22 leagues) matches the literature exactly. No published peer-reviewed football study we found shows a public-data model beating sharp closing odds on proper scoring rules out of sample.
- "Inefficiency" in this literature almost always means **soft-bookmaker prices diverging from the consensus or exchange**, or FLB in average odds. It rarely means the consensus itself being wrong. The practical edge therefore comes from price discovery (sharp or consensus → soft), not from out-predicting the market.
- The devigging method matters for any value-bet scanner. Plain normalisation (equal margin) overstates the value of longshots.

### Gaps
- Could not access the full text of Angelini & De Angelis (2019) to identify which leagues were inefficient, nor Winkelmann et al. (2024) for league-by-league numbers.
- No peer-reviewed study found that directly compares Pinnacle **opening** vs **closing** football odds on log-loss.

## 2. Which published approaches claim out-of-sample profit, and how robust are they?

### Takeaway
The most robust documented edge is **consensus/sharp-price arbitrage against soft bookmakers**: Kaunitz et al.; Buchdahl's Wisdom of the Crowd; Franck et al.'s exchange-vs-bookmaker work. Yields are about 2-10% pre-tax, and the edge is limited by account restrictions. Fundamental-model profits (pi-ratings, GAP, Poisson variants, xG, ML) are small (about 0.8-5%). They usually require **best-available (max) odds**, turn into losses at average odds, or rest on small samples and ex-post threshold tuning.

### Cited Findings
- **Kaunitz, Zhong & Kreiner (2017), "Beating the bookies with their own numbers"** [WP, arXiv; we found no peer-reviewed version] — [arXiv 1710.02824](https://arxiv.org/abs/1710.02824) ([PDF](https://arxiv.org/pdf/1710.02824))
  - **Rule:** bet when the maximum odds exceed 1/(p_cons - 0.05). p_cons is the consensus probability, 1/mean odds of 32 bookmakers minus a margin constant (0.034 home, 0.057 draw, 0.037 away). α = 0.05 was chosen by scanning 0.01-0.10 on the backtest, so it is in-sample.
  - **Closing-odds backtest:** Jan 2005 to Jun 2015, 479,440 games in 818 leagues. 56,435 bets, 44.4% hit rate, **+3.5% yield**. Random bets at max odds returned -3.32%; the strategy was 10.8 SD above that.
  - **Intraday odds:** 1-5 h before kick-off, 31,074 games (1 Sep 2015 to 29 Feb 2016). 6,994 bets, mean odds 2.32, **+9.9% yield**.
  - **Paper trading, 3 months:** 407 bets, +5.5%.
  - **Real money, 5 months:** 265 bets at $50, +$957.50, **+8.5%**. Combined paper and real: 672 bets, +6.2%.
  - **Latency:** about 30% of odds shown on the dashboard had already changed when checked at the bookmaker.
  - **Restrictions:** "a few months" into real-money betting, bookmakers limited stakes, required "manual inspection" or offered less than $50, and the authors stopped.
- **Buchdahl, "Wisdom of the Crowd" (2015, updated 2017).** Best market prices (Betbrain) vs Pinnacle-implied fair odds, collected at the same time (neither opening nor closing). Sample: 37,303 matches, 22 European divisions, 27 Jul 2012 to 17 May 2017. [Blog] — [PDF](https://www.football-data.co.uk/The_Wisdom_of_the_Crowd_updated.pdf)
  - **All value > 0%:** 36,496-36,529 bets; actual yield +2.32% (logarithmic devig, expected 2.31%, average odds 3.36) or +2.36% (odds-ratio, expected 2.44%, average odds 3.7).
  - **Value > 3%:** 9,193-9,542 bets; actual +5.9% to +7.2%.
  - **Value > 5%:** 3,508-4,038 bets; actual +8.8% to +13.45%, expected about 8.1-8.7%, average odds 6-7.
  - Buchdahl notes that the bookmakers offering such prices regularly restrict or ban winners.
- **[Sec]** A secondary site reports Buchdahl's own live record at "3.7% profit over 18,000 bets since 2015", against a predicted 4.0%. It also reports a 14-season backtest of 31,247 bets on other bookmakers priced above Pinnacle, with 3.8% expected and 3.6% actual ROI. Unverified at primary source — [pinnacleoddsdropper.com blog](https://www.pinnacleoddsdropper.com/blog/closing-line-value--clv-demystified-by-expert-joseph-buchdahl)
- **Wheatcroft (2020), GAP ratings, over/under 2.5 goals** [PR] — [IJF 36(3):916-932](https://ideas.repec.org/a/eee/intfor/v36y2020i3p916-932.html); [accepted MS](https://researchonline.lse.ac.uk/id/eprint/103712/1/Predict_total_goals_LSE.pdf)
  - 10 European leagues, 12 years.
  - With shots + corners as inputs and **max BetBrain odds**: 68,672 bets, **about +0.8% per bet**. No random-bet scenario in 1 million trials matched it.
  - With goals as input: a large loss.
  - With **average odds** (overround about 6.8%): "a substantial loss" under both level and Kelly staking.
- **Constantinou & Fenton (2013), pi-ratings** [PR] — [JQAS 9(1):37-50 (pre-print)](http://www.constantinou.info/downloads/papers/pi-ratings.pdf)
  - EPL 2007/08 to 2011/12, 1,887 bets, staked £1,887, returned £1,944.62 (**about +3.05%**).
  - Mostly longshots (mean odds about 3.1-3.3, win rate 28-37%).
  - Learning rates were tuned on profitability.
- **Constantinou (2022), Asian handicap vs 1X2, Bayesian network + ratings** [PR] — [J. Sports Analytics 8(3):171-193; arXiv 2003.09384](https://arxiv.org/pdf/2003.09384)
  - 13 EPL seasons (2006/07 to 2018/19), static thresholds (Table 9). The paper calls these the only realistic results.
  - **1X2 at average odds:** -9.03% at a 0% threshold (4,334 bets); negative through a 6% threshold; +3.67% at 7% (1,036 bets); +5.49% at 8% (814 bets); +2.34% at 10% (452 bets).
  - **1X2 at max odds:** +0.96% (4%, 3,067 bets) up to +16.89% (9%, 1,049 bets).
  - **AH at average odds:** -3.86% (3,914 bets) to +0.26% (9%, 936 bets).
  - **AH at max odds:** -1.35% to +2.94% (10%, 1,080 bets).
  - The optimal threshold "varies dramatically between seasons". Per-season optimised results are "not a realistic expectation of real-world performance". Max odds raise profits by 42-296% relative to average odds.
- **Koopman & Lit (2015), dynamic bivariate Poisson** [PR] — [JRSS A 178(1):167-186](https://rss.onlinelibrary.wiley.com/doi/10.1111/rssa.12042); [TI WP](https://papers.tinbergen.nl/12099.pdf)
  - Out-of-sample EPL 2010/11 to 2011/12, average odds of 28-40 bookmakers (bookmaker edge about 7%).
  - Mean returns are around zero for thresholds up to 0.12. At threshold 0.40, only 50 bets over two seasons, with expected return about +50%. The sample is tiny.
- **Egidi, Pauli & Torelli (2018)**, hierarchical Bayesian Poisson combining odds and results. Top-4 leagues, 9 seasons training → 10th season. They report positive *expected* profits for each league and bookmaker, but these are computed under the model's own probabilities, not realised returns, so the evidence is weak. [PR] — [Statistical Modelling 18:436-459; arXiv 1802.08848](https://arxiv.org/pdf/1802.08848)
- **Wilkens (2026), xG-Skellam model with isotonic calibration** [PR, open access] — [J. Sports Analytics 12 (2026)](https://journals.sagepub.com/doi/10.1177/22150218261416681)
  - Bundesliga 2014/15 to 2024/25. Bookmaker odds are better calibrated.
  - Simulated betting: **about 10% ROI at average odds, about 15% at best prices**. Profits come mostly from home-win bets; away-win bets lose consistently. Profitability "varies considerably by season and bet type".
  - Caution: a published figure refers to a grid search for parameter optimisation. We could not verify whether thresholds were selected out of sample.
- **Reade, Singleton & Vaughan Williams (2020), EPL scorelines** [WP] — [Reading DP 2020-03](https://www.reading.ac.uk/web/files/economics/emdp202003.pdf)
  - Two EPL seasons. The model's scoreline probabilities encompass (are better than) bookmaker-implied ones.
  - A simple betting strategy produced "no substantial or consistent financial returns" in the result, scoreline, margin or total-goals markets. Scoreline overround was about 12%.
- **da Costa, Marinho & Pires (2022), "both teams to score" (BTTS)** [PR]. ML classifiers can in "some scenarios" outperform bookmakers and yield profitable strategies. We could not retrieve the yield or bet counts. — [IJF 38(3):895-909](https://ideas.repec.org/a/eee/intfor/v38y2022i3p895-909.html)
- **Clegg, Song & Cartlidge (2026), in-play** [WP]. A Weibull accelerated-failure-time model calibrated to Betfair kick-off prices, with post-shot xG. On 140 EPL matches it roughly matches Betfair accuracy (70.2% vs 70.6%). A simulated **4.5% ROI over 17,458 in-play bets** (Sharpe 5.94). Caution: only 140 matches, so bets are heavily correlated. — [arXiv 2605.16066](https://arxiv.org/pdf/2605.16066)
- **Event and regime-shift edges.**
  - **COVID ghost games (Bundesliga 2019/20):** bookmakers did not reprice the lost home advantage; ROI "around fifteen percent" on 83 matches. [WP/arXiv] — [Deutscher, Winkelmann & Ötting 2020](https://arxiv.org/pdf/2008.05417)
  - **Promoted teams (German football):** backing promoted teams' away wins early in the season gave a temporarily positive ROI; the inefficiency is "short-lived". [PR] — [Deutscher, Frick & Ötting 2018, Applied Economics 50(30)](https://ideas.repec.org/a/taf/applec/v50y2018i30p3240-3246.html)
- **Hubáček & Šír (2023), "Beating the market with a bad predictive model"** [PR]. They train models decorrelated from market prices and show profit is possible with an inferior predictor. The empirical tests use **NBA** betting, not football. — [IJF 39(2); arXiv 2010.12508](https://arxiv.org/abs/2010.12508). (The frequently cited Hubáček, Šourek & Železný 2019 IJF "Exploiting sports-betting market using machine learning" is also basketball; not re-verified here.)
- **Wunderlich & Memmert (2020).** Positive betting returns can arise without a superior forecasting model. Returns are a noisy measure of accuracy, and accuracy measures should be preferred unless profit is the target. [PR] — [IJF 36(2):713-722](https://ideas.repec.org/a/eee/intfor/v36y2020i2p713-722.html)

### Inferences
- A consistent pattern runs through Wheatcroft, Constantinou (2022) and Wilkens (15% vs 10%): **the same forecasts turn from loss to profit when "max odds across all bookmakers" replace average odds.** Max odds include stale prices, promotional "loss leaders" and books that restrict winners, as Hegarty & Whelan argue for excluding them ([WP23/05](https://www.ucd.ie/economics/t4media/WP23_05.pdf)). Profits at max odds should not be treated as achievable at a single Polish licensed bookmaker.
- The approaches with documented live money (Kaunitz: 265 real bets; Buchdahl's live record, secondary) are **price-comparison strategies**, not better predictive models. Model Buk's "market-residual" direction is conceptually closer to these than to Dixon-Coles-type fundamentals, but it needs a sharp reference price and a soft target price.
- Longshot-heavy selections (pi-ratings average odds about 3.1; Constantinou 1X2 average above 3; Buchdahl's high-value buckets at average odds 6-7) are exactly where FLB-driven devig errors inflate apparent edges (Section 4).

### Gaps
- No verified numbers for the BTTS paper (IJF 2022) or for Deutscher et al. (2018).
- We could not confirm whether Wilkens (2026) tuned betting thresholds out of sample.
- We found no peer-reviewed football-specific 2020-2026 deep-learning paper with documented out-of-sample profit against closing odds.

## 3. Are niche markets (corners, cards, AH, props) or lower leagues less efficient, and by how much?

### Takeaway
Lower leagues carry **higher margins and thinner liquidity** but show no systematic, persistent mispricing in the best-quality studies. The Asian-handicap market is **more** efficient than 1X2: no FLB, lower margins. Exact-score markets show FLB but carry about 12% overround. We found **no peer-reviewed profitability evidence for corners, cards, shots or player props**.

### Cited Findings
- **English Premier League vs lower divisions (Elaad et al. 2020).** Lower tiers had larger overround and lower predictability. There was no significant evidence of inefficiency in any of the 4 tiers from 2010/11 to 2017/18. Exchange volume per match fell from £23k (PL) to under £1k (League Two). [PR] — [Elaad, Reade & Singleton](https://www.reading.ac.uk/web/files/economics/emdp201910.pdf)
- **Margins by league 2005/06-2011/12 (Constantinou & Fenton 2013).** Average bookmaker margins were roughly 6-12%, declining over the period and higher in lower English divisions. The authors claim profitable odds biases and numerous arbitrage opportunities across 14 leagues. [PR] — [J. Gambling Business & Economics](http://constantinou.info/downloads/papers/evidenceofinefficiency.pdf)
- **League-level persistence.** Angelini & De Angelis (2019): 3 (or 4) of 11 leagues were inefficient at best odds ([RePEc](https://ideas.repec.org/a/eee/intfor/v35y2019i2p712-721.html)). Winkelmann et al. (2024) found such single-season or league inefficiencies do not persist beyond chance ([RePEc](https://ideas.repec.org/a/sae/jospec/v25y2024i1p54-97.html)).
- **Asian handicap vs 1X2 (Hegarty & Whelan).** Sample: 84,230 matches, 22 European leagues, 2011/12 to 2021/22, average closing odds. AH bets show no clear FLB and less dispersion in returns; average AH odds are 1.92. The AH market's low margins and willingness to take large bets indicate higher competition. [WP → PR IJF] — [WP23/05](https://www.ucd.ie/economics/t4media/WP23_05.pdf)
- **AH loss rates by handicap type (Hegarty & Whelan, "Returns on complex bets").** Average loss rates are 4.16% for half-goal handicaps (no refund), 3.61% and 3.57% for .25 and .75 lines, and 3.24% for integer lines. The pattern is predictable from the odds, which the authors attribute to bettors miscalculating expected losses. [PR] — [Review of Behavioral Finance 16(5)](https://www.emerald.com/rbf/article/16/5/904/1217616/Returns-on-complex-bets-evidence-from-Asian); [PDF](https://www.karlwhelan.com/Papers/ComplexBets.pdf)
- **AH vs 1X2 profitability (Constantinou 2022).** The EPL AH market "shares the inefficiencies of the traditional market", but model profits and ROI are about 2.5-5.5x and 2.5-4x **lower** than in 1X2. Average AH ROI at average odds is negative for nearly all thresholds. [PR] — [arXiv 2003.09384](https://arxiv.org/pdf/2003.09384)
- **Over/under 2.5 goals (Wheatcroft 2020).** Blind "over" betting lost -1.81% and blind "under" lost -1.46%, a small under-bias that cannot be exploited. The model profits only at max odds (+0.8%). [PR] — [LSE accepted MS](https://researchonline.lse.ac.uk/id/eprint/103712/1/Predict_total_goals_LSE.pdf)
- **Exact scorelines.** Bookmaker odds show significant FLB, unlike match results. Overround is about 12%, and a model with better scoreline probabilities still made no consistent profit. [WP] — [Reade, Singleton & Vaughan Williams 2020](https://www.reading.ac.uk/web/files/economics/emdp202003.pdf); related [Reade, Singleton & Brown 2021, SJPE 68(2)](https://ideas.repec.org/a/bla/scotjp/v68y2021i2p261-285.html)
- **Corners.** A compound-Poisson model for corner counts uses information from cross-market betting odds to improve prediction. No betting-profit evaluation was found in the abstract. [WP] — [arXiv 2112.13001](https://arxiv.org/pdf/2112.13001)
- **Sentiment in Spanish football (Forrest & Simmons 2008, pre-2010 but foundational).** Sample: more than 3,000 bets on La Liga. Odds were influenced by club fan-base size: supporters of the more popular team got more favourable terms, with similar findings for Scotland. [PR] — [Applied Economics 40(1); pre-print](https://eprints.lancs.ac.uk/id/eprint/44748/1/10.pdf)
- **Market liquidity (Hegarty & Whelan).** "Much of the volume" on European football is placed with specialist bookmakers such as Pinnacle in the AH market. [WP] — [WP23/05](https://www.ucd.ie/economics/t4media/WP23_05.pdf)

### Inferences
- For a scanner, lower leagues and niche markets offer **wider soft-bookmaker price dispersion** (higher margins, fewer sharp reference prices). But there is no peer-reviewed evidence of a model-based edge big enough to overcome a 12% tax. The same thin liquidity also means sharp reference prices (needed for value detection) are less reliable there.
- Props, cards and corners are genuinely under-researched. Any edge claim there would be novel, and it needs Model Buk's own out-of-sample data plus a reliable sharp reference line (often unavailable).

### Gaps
- We found no academic study quantifying mispricing or yield for corners, cards, shots or player-prop markets in football. Only industry blogs and student theses (e.g., [Lund thesis on corner odds](https://lup.lub.lu.se/student-papers/record/9127007/file/9127013.pdf), not reviewed) exist.
- No study found that quantifies soft-bookmaker margins by league tier after 2018.

## 4. Favourite-longshot bias in football: direction and magnitude

### Takeaway
In average 1X2 odds across European leagues, FLB is **strong, nonlinear and in the classic direction**: longshots lose far more. In the lowest probability decile the loss is about 17% per unit, against about 2% in the top decile. It is weaker or absent at Pinnacle once the margin is removed with a longshot-aware method, on exchanges, and in Asian handicaps. English-league-only tests with regression methods found no significant FLB, likely partly for methodological reasons.

### Cited Findings
- **Hegarty & Whelan (84,230 matches, 22 leagues, 2011/12 to 2021/22, average closing odds).**
  - Lowest probability decile (mean p about 14%): average payout $0.83 per $1, a **-17%** loss.
  - Highest decile (p about 63%): payout $0.98, a **-2%** loss. The pattern is "strongly nonlinear".
  - Expected average loss under efficiency was 6.5%; actual loss betting all outcomes was 7.8% (7.7% excluding draws). t-tests reject equality.
  - [WP → PR IJF] — [WP23/05](https://www.ucd.ie/economics/t4media/WP23_05.pdf); [IJF](https://www.sciencedirect.com/science/article/pii/S0169207024000670)
- **Test methodology (Hegarty & Whelan 2024).** Regressions on inverse decimal odds are biased *against* finding FLB; the normalised-probability method gives valid tests. They validate this on soccer and tennis data. [WP, CEPR DP 19486] — [RePEc](https://ideas.repec.org/p/pra/mprapa/121382.html)
- **English leagues (Elaad et al. 2020).** In English leagues 2010/11 to 2017/18, no significant FLB was found. The PL point estimate is in the *reverse* direction (bookmakers underestimate longshots). Angelini & De Angelis found a small, insignificant positive FLB for the PL. [PR] — [Elaad et al.](https://www.reading.ac.uk/web/files/economics/emdp201910.pdf)
- **Franke (2020).** Finds FLB in European soccer betting in both bookmaker and exchange market structures; lottery-type bets are mispriced. [PR] — [QREF 75:1-18](https://ideas.repec.org:443/a/eee/quaeco/v75y2020icp1-18.html)
- **Pinnacle closing odds.** Removing the margin with methods that weight it toward longshots gives about 0% blind yield. Equal-margin normalisation gives -0.56%, and flagged 3,957 of 111,909 odds as "value" that the other three models rejected. [Blog] — [Buchdahl](https://www.football-data.co.uk/The_Wisdom_of_the_Crowd_updated.pdf)
- **Theory (Whelan 2024).** In competitive fixed-odds markets, disagreement among gamblers plus bookmaker risk aversion generates FLB. [PR] — [Economica 91(361):188-209](https://ideas.repec.org/a/bla/econom/v91y2024i361p188-209.html)
- **Exact scorelines.** Significant FLB. [WP] — [Reade et al. 2020](https://www.reading.ac.uk/web/files/economics/emdp202003.pdf)

### Inferences
- FLB means that "blind" value-betting on longshots at soft books, with probabilities devigged by simple normalisation, will systematically overstate edges. Model Buk should devig reference prices with Shin, power/log, odds-ratio or an FLB-adjusted GLM, and evaluate yield separately by odds band.
- FLB makes favourites relatively cheaper, but even the top decile loses about 2% at average odds. That is before a 12% tax, so no FLB-harvesting strategy survives the tax.

### Gaps
- No recent (post-2022) league-by-league FLB magnitudes were verified, nor FLB for Polish-licensed bookmakers specifically.

## 5. Timing: when is value most likely, and how fast do soft bookmakers follow sharp moves?

### Takeaway
Evidence points to **pre-closing windows** as the place where soft-bookmaker prices deviate from consensus or sharp prices. Kaunitz found +9.9% at 1-5 h before kick-off vs +3.5% at closing odds. Such deviations are short-lived: about 30% had moved by the time a human checked. The closing sharp price is the best benchmark of whether a bet had value. There is no peer-reviewed measurement of soft-bookmaker lag, in minutes, behind sharp moves in football.

### Cited Findings
- **Kaunitz et al.** Consensus-deviation betting earned +3.5% on closing odds (10-year backtest) but +9.9% when bets were placed 1-5 h before kick-off (6,994 bets, Sep 2015 to Feb 2016). About 30% of dashboard odds had "already been changed" at the bookmaker when checked, a delay the authors attribute to the bookmakers' own sites and scrapers. [WP] — [arXiv 1710.02824](https://arxiv.org/pdf/1710.02824)
- **Closing-line value.** The pre-closing/closing odds ratio predicts yield about 1:1 (87,960 Pinnacle odds pairs). [Blog] — [Buchdahl 2016](https://www.football-data.co.uk/blog/pinnacle_efficiency.php)
- **Soft books follow sharp books.** Soft bookmakers' AH odds largely follow sharp bookmakers', and sharp books sell probabilities to soft books. [WP] — [Hegarty & Whelan WP23/05](https://www.ucd.ie/economics/t4media/WP23_05.pdf)
- **Bookmakers ignore rival information.** Bookmakers do not fully incorporate competitors' odds (statistically significant, economically tiny) — [Elaad et al.](https://www.reading.ac.uk/web/files/economics/emdp201910.pdf). They also fail to incorporate exchange odds, which creates arbitrage in 19.2% of Big-5 matches — [Franck et al. 2013](https://www.wiwi.uni-muenster.de/uf/sites/uf/files/PublikationenNuuesch/2013franck_verbeek_nueesch_2013.pdf).
- **In-play (1 Hz Bundesliga data, one season).** Neither the bookmaker nor bettors anticipate the first goal before it happens. [WP] — [Winkelmann & Deutscher 2025, arXiv 2505.21275](https://arxiv.org/pdf/2505.21275)
- **In-play momentum.** After equalisers, bettors stake about 40% more on the team with "momentum", but momentum does not predict outcomes and backing it loses heavily. [WP] — [Ötting, Deutscher, Singleton & De Angelis 2022, arXiv 2211.06052](https://arxiv.org/pdf/2211.06052)
- **Sharp vs average prices.** Ignoring sharp and exchange prices (average bookmaker odds) costs about -11.3% per bookmaker bet, against -2.5% on the exchange (Big-5, 2004-2011). [PR] — [Franck et al. 2013](https://www.wiwi.uni-muenster.de/uf/sites/uf/files/PublikationenNuuesch/2013franck_verbeek_nueesch_2013.pdf)

### Inferences
- For an hourly or daily scanner, the literature suggests value appears as **soft-book prices that lag a sharp or consensus move**. Hourly polling will miss many of the moves that Kaunitz saw vanish within minutes, and daily polling will miss most. The most defensible approach is to log the Pinnacle (or consensus) price at bet time **and** at close, then measure closing-line value (CLV).
- Lineup news (about 1 h pre-KO) is widely described by industry sources as the most volatile window, but academic confirmation for football is lacking (Gaps).

### Gaps
- No peer-reviewed football study quantifies (a) soft-bookmaker reaction lag to sharp moves, or (b) odds reaction to official lineup announcements. Only industry blogs were found, e.g., [jackpotinsights substack](https://jackpotinsights.substack.com/p/why-football-betting-odds-change), which is not reliable.
- No verified academic comparison of opening vs closing Pinnacle accuracy for football.

## 6. What sample sizes are needed to separate a real edge from luck?

### Takeaway
Profit/loss is an extremely noisy test. At typical odds of about 2-3.5, confirming a 2-3% yield takes roughly 5,000-25,000 bets. A 5% yield needs about 1,000-4,000 bets, and even a 13.6% yield (the tax break-even) needs hundreds. CLV-based evaluation needs far fewer bets. Season-level "significant" inefficiencies are often chance.

### Cited Findings
- **Winkelmann et al. (2024).** Small samples make markets appear inefficient, and "inefficient periods" occur within fully efficient simulated markets at about the observed frequency. [PR] — [RePEc](https://ideas.repec.org/a/sae/jospec/v25y2024i1p54-97.html)
- **Constantinou (2022).** Even with at least 30 bets per season, "the sample size remains insufficient for deriving reliable and robust single-season conclusions". Thresholds optimised per season overstate real performance. [PR] — [arXiv 2003.09384](https://arxiv.org/pdf/2003.09384)
- **Wunderlich & Memmert (2020).** Betting returns are a noisy, less interpretable accuracy measure than scoring rules. [PR] — [IJF 36(2)](https://ideas.repec.org/a/eee/intfor/v36y2020i2p713-722.html)
- **[Sec] CLV vs P/L (attributed to Buchdahl's *Monte Carlo or Bust*).** The SD of CLV per bet is about 0.1, against about 1.0 for even-money P/L, so "as few as just 50" bets can show significance if the closing line is consistently beaten. For about 1.7% expected value, "many thousands" of bets are needed on P/L. — [pinnacleoddsdropper summary](https://www.pinnacleoddsdropper.com/blog/closing-line-value--clv-demystified-by-expert-joseph-buchdahl)
- **Significance tests in Kaunitz et al.** The 56,435-bet backtest was 10.8 SD above random betting; the 6,994-bet sample was 4.8 SD above. The 672 live and paper bets were significant only against a random-bet benchmark of -0.7%. [WP] — [arXiv 1710.02824](https://arxiv.org/pdf/1710.02824)

### Inferences
Our own calculation assumes level stakes and bets with a true yield y at decimal odds O. The per-bet standard deviation is O·√(p(1−p)) with p = (1+y)/O. Bets needed: n = (z·SD / y)², testing against 0% yield.

| True yield | Odds 1.9 (80% power / 2σ) | Odds 2.5 | Odds 3.5 | Odds 5.0 |
|---|---|---|---|---|
| 2% | 13,900 / 9,000 | 23,300 / 15,100 | 39,100 / 25,300 | 62,800 / 40,600 |
| 3% | 6,200 / 4,000 | 10,400 / 6,700 | 17,500 / 11,300 | 28,100 / 18,200 |
| 5% | 2,200 / 1,400 | 3,800 / 2,400 | 6,400 / 4,100 | 10,300 / 6,600 |
| 10% | 540 / 350 | 950 / 620 | 1,630 / 1,060 | 2,650 / 1,720 |
| 13.6% | 290 / 190 | 520 / 340 | 900 / 580 | 1,470 / 950 |

80% power uses a one-sided α = 0.05 (z_α + z_β = 2.49). The 2σ column uses z = 2. Since bets on the same match or matchday are correlated, the effective n is smaller.

- "A few high-confidence value bets" per scan (say 3-10 per day) means 1,000-3,500 bets per year. That can only confirm yields of at least about 5-10% on P/L within a year, so CLV against Pinnacle close should be the primary live metric.

### Gaps
- We could not retrieve the primary Buchdahl text (*Monte Carlo or Bust*) or Pinnacle articles; the pinnacle.com connection was refused. The CLV-SD figure is second-hand.

## 7. Size of documented edges vs a 12% stake tax (Model Buk context)

### Takeaway
A 12% stake tax means effective odds = 0.88 × quoted odds. A bet is +EV after tax only if p·O ≥ 1/0.88 = **1.136**, i.e., the quoted price must be **at least 13.6% above fair**. Almost no robust, large-sample edge in the literature reaches that. The consensus-arbitrage edges (about 2-10%) and model edges (about 0.8-5%) would all become losses. Only small-sample or regime-shift results (Koopman-Lit's 50 bets, COVID ghost games, Wilkens' best-price 15%) exceed it, and none is robust.

### Cited Findings
Summary of documented yields, all pre-tax:
| Study | Type | Bets / period | Yield | Odds basis | Robustness |
|---|---|---|---|---|---|
| Kaunitz et al. 2017 [WP](https://arxiv.org/abs/1710.02824) | consensus vs soft | 56,435 (2005-15 backtest) | +3.5% | max closing | in-sample α; accounts limited |
| same | same | 6,994 (1-5 h pre-KO, 2015/16) | +9.9% | max intraday | about 30% of flagged prices gone on checking |
| same | same | 265 real-money bets (5 months) | +8.5% | actual | stopped by restrictions |
| Buchdahl WoC [Blog](https://www.football-data.co.uk/The_Wisdom_of_the_Crowd_updated.pdf) | Pinnacle-fair vs best price | about 36,500 (2012-17) | +2.3-2.5% | best market | actual ≈ expected |
| same | same | 3,508-4,038 (value > 5%) | +8.8% to +13.5% | best market (average odds 6-7) | longshot-heavy |
| Franck et al. 2013 [PR](https://www.wiwi.uni-muenster.de/uf/sites/uf/files/PublikationenNuuesch/2013franck_verbeek_nueesch_2013.pdf) | bookmaker vs exchange arbitrage | 2,287 arbitrages (2004-11) | +1.4% risk-free | bookmaker + Betfair | needs exchange access |
| Wheatcroft 2020 [PR](https://ideas.repec.org/a/eee/intfor/v36y2020i3p916-932.html) | GAP ratings O/U | 68,672 (12 years) | +0.8% | max odds (loss at average) | robust vs random |
| Constantinou & Fenton 2013 [PR](http://www.constantinou.info/downloads/papers/pi-ratings.pdf) | pi-ratings 1X2 | 1,887 (EPL 2007-12) | about +3.1% | market odds | small N, tuned |
| Constantinou 2022 [PR](https://arxiv.org/pdf/2003.09384) | BN 1X2 / AH | 452-4,334 (EPL 13 seasons) | -9% to +5.5% (average odds) | average / max | threshold unstable |
| Koopman & Lit 2015 [PR](https://papers.tinbergen.nl/12099.pdf) | bivariate Poisson | 50 (at κ = 0.40) | about +50% expected | average odds | tiny N |
| Wilkens 2026 [PR](https://journals.sagepub.com/doi/10.1177/22150218261416681) | xG-Skellam | Bundesliga 2014/15-2024/25 | about +10% / +15% | average / best | season-variable; tuning unclear |
| Clegg et al. 2026 [WP](https://arxiv.org/pdf/2605.16066) | in-play AFT + xG | 17,458 bets / 140 matches | +4.5% | Betfair in-play | correlated bets |
| Deutscher et al. 2020 [WP](https://arxiv.org/pdf/2008.05417) | COVID home advantage | 83 matches | about +15% | market | one-off regime |

- **Hegarty & Whelan.** Bettors who pick only the best available odds "usually find the amounts that can be placed at those odds are small and those with a record of making profits tend to be banned". [WP] — [WP23/05](https://www.ucd.ie/economics/t4media/WP23_05.pdf)
- **Kaunitz et al.** Account limits arrived within months of real-money betting. [WP] — [arXiv 1710.02824](https://arxiv.org/pdf/1710.02824)

### Inferences
- **Break-even arithmetic.** With the 12% stake tax, only 0.88 of the stake is effectively wagered. Expected return = 0.88·p·O − 1.
  - Break-even needs a quoted price 13.6% above fair. For example, fair 2.00 needs odds of at least 2.27, and fair 3.00 needs at least 3.41.
  - A zero-skill bettor facing a bookmaker margin m expects 0.88/(1+m) − 1: about -16% at m = 5% and -18.5% at m = 8%.
  - **Model Buk's simulated -11% to -27% yields are therefore consistent with no edge plus margin plus tax** (with noise). This is not evidence of a broken model.
- The largest robust edges in the literature (consensus/sharp vs soft; up to about +9.9% intraday for Kaunitz, about +13% for Buchdahl's high-value longshots) would sit at roughly -3% to 0% after tax. They are also exactly the bets soft bookmakers restrict first.
- A realistic research target for Model Buk is therefore not "beat closing odds with a better model". It is either:
  1. **very selective sharp-vs-soft price discrepancies of at least 14-15% vs devigged Pinnacle or consensus.** These are rare, often longshots or stale prices, and must be validated by CLV. Or
  2. **documenting that no taxed strategy is +EV.** This is itself a valid research finding and consistent with the literature.
- Recommended live validation metric: CLV against devigged Pinnacle closing odds (Shin, power or odds-ratio devig), evaluated per odds band, with tax-adjusted break-even at CLV ≥ 13.6%.

### Gaps
- No academic study was found that evaluates football betting strategies under a stake-based turnover tax such as Poland's 12%.
- Not verified here: whether Polish-licensed bookmakers absorb the tax in some offers, whether Pinnacle or exchange prices can legally be *bet* from Poland (they can still be used as reference prices), and Polish bookmakers' typical margins.
- We did not find how often soft-book prices exceed devigged Pinnacle fair odds by at least 13.6% in top-20 European leagues. This would need Model Buk's own odds-snapshot data. Buchdahl's value > 5% bucket was only about 3,500-5,100 of 111,909 best-price odds (about 3-5%), so the ≥13.6% share is presumably much smaller (inference).
