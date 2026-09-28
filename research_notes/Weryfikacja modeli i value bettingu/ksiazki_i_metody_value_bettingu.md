# Books and practitioner methods for finding football value bets (with the Polish 12% stake tax)

Research notes, compiled 2026-09-28 for Model Buk. Access problems during research: pinnacle.com (Pinnacle Betting Resources) returned connection refused from this environment. That fits Pinnacle being blocked for Polish users (see Q4), but I have not confirmed the cause. football-data.co.uk returned HTTP 429 (rate limit) after one successful fetch. For those reasons some Buchdahl/Pinnacle articles are cited through secondary summaries, and each one is marked as such. No betexplorer/oddsportal scraping was done.

Evidence labels used below: **[EVIDENCE]** = peer-reviewed or reproducible data analysis; **[AUTHOR DATA]** = the author's own published track record or backtest, not independently audited; **[PRACTITIONER]** = experienced practitioner opinion; **[MARKETING]** = vendor, affiliate or tipster claim with a commercial interest; **[ANECDOTAL]** = forum or blog experience.

---

## Q1. What do the key books teach? (annotated bibliography)

### Takeaway
Most of the useful, evidence-based material comes from Joseph Buchdahl: market efficiency, Pinnacle as the "true price", margin removal, luck-vs-skill testing and Monte Carlo variance. Supporting sources are Miller & Davidow (how odds markets form), Kaunitz et al. 2017 (a replicable soft-vs-sharp strategy with a real-money test) and the Kelly literature (Poundstone; Uhrín et al. 2021). Popular analytics books (Soccermatics, The Numbers Game, Trading Bases, The Signal and the Noise) are useful for modelling ideas and mindset. They do not provide proven football betting methods, and their track records are short or anecdotal. Polish-language books are basic and have no evidence base.

### Cited Findings

#### Joseph Buchdahl (High Stakes Publishing; also runs football-data.co.uk), the most relevant author for this project
- Buchdahl's books are *Fixed Odds Sports Betting*, *How to Find a Black Cat in a Coal Cellar*, *Squares & Sharps, Suckers & Sharks* and *Monte Carlo or Bust*. He has worked as a betting analyst for about 20 years and provides historical data and odds through Football-Data.co.uk and Tennis-Data.co.uk — [Goodreads author page](https://www.goodreads.com/author/show/449609.Joseph_Buchdahl); [Google Books – Monte Carlo or Bust](https://books.google.com/books/about/Monte_Carlo_Or_Bust.html?id=vO94zgEACAAJ)
- **Fixed Odds Sports Betting: Statistical Forecasting and Risk Management (2003, High Stakes).** Uses numerical techniques to show how fixed-odds punters can beat the bookmaker, protect profits through sensible risk management, and turn high-risk gambling into "low-risk investment". It covers statistical forecasting/rating models and staking and risk management. Credibility: an early, quantitative treatment. Some of the market context (pre-2003, before the rise of exchanges and Asian books) is dated — [World of Books listing](https://www.worldofbooks.com/products/fixed-odds-sports-betting-book-joseph-buchdahl-9781843440192); [Google Books](https://books.google.com/books/about/Fixed_Odds_Sports_Betting.html?id=ScL0CQAAQBAJ)
- **How to Find a Black Cat in a Coal Cellar: The Truth About Sports Tipsters.** About telling genuinely skilled tipsters from ones whose profits come from luck and circumstance. This is his luck-vs-skill (t-test / p-value) book — [Sporting Life guide referencing the book](https://www.sportinglife.com/free-bets/guides/advanced/how-to-identify-a-good-tipster); [Goodreads author page](https://www.goodreads.com/author/show/449609.Joseph_Buchdahl)
- **Squares & Sharps, Suckers & Sharks: The Science, Psychology & Philosophy of Gambling** (first ed. ISBN 9781843448587; later ed. ISBN 9780857304841). Covers probability and uncertainty, expectation vs utility, human irrationality, luck and skill, market efficiency and the wisdom of crowds. This is the conceptual basis for "Pinnacle as the true price" value betting — [Amazon (1st ed.)](https://www.amazon.com/Squares-Sharps-Suckers-Sharks-Psychology/dp/1843448580); [Amazon (new ed.)](https://www.amazon.com/Squares-Sharps-Suckers-Sharks-Psychology/dp/0857304844)
- **Monte Carlo or Bust: Simple Simulations for Aspiring Sports Bettors** (ISBN 9780857304858). Explains how to use Monte Carlo simulation to understand the range of outcomes and the variance of a betting strategy, and so improve betting quality — [Amazon](https://www.amazon.com/Monte-Carlo-Bust-Simulations-Aspiring/dp/0857304852); [Pinnacle book review](https://www.pinnacle.com/betting-resources/en/educational/book-review-monte-carlo-or-bust/sha274ccu45t7n7b) (review page not reachable from this environment)
- **Long-form article, "Using the Wisdom of the Crowd to Find Value in a Football Match Betting Market"** (20-page PDF). Derives fair odds from Pinnacle prices using a margin model in which the margin grows with the odds: `Of = 3O/(3 − M·O)`. Dataset: 22,318 European matches, 2012/13–2014/15. Betting all prices longer than fair gave a 3.4% yield (22,281 bets). A 2% advantage filter gave 6.63% (9,196 bets). A 5% filter gave 13.22% (1,927 bets). Backing the Pinnacle closing prices themselves (with margin) gave about 0.08%, i.e. break-even. It warns that customers who repeatedly take superior prices "can often expect to have their betting activity curtailed" **[AUTHOR DATA, reproducible from football-data.co.uk CSVs]** — [football-data.co.uk blog](https://www.football-data.co.uk/blog/wisdom_of_the_crowd.php); [PDF](https://www.football-data.co.uk/The_Wisdom_of_the_Crowd_updated.pdf)
- In Buchdahl's own Pinnacle-based value betting (summarised by a commercial odds-tool site), he bets only when expected value exceeds **102%**, which leaves a safety margin. In a retrospective test of about 67,000 odds, predicted profit was 2.2% and actual 3.3%. Forward tracking from August 2015 covered about 18,000 value odds, with 4.0% predicted and 3.7% actual; neither difference was statistically significant. He suggests small stakes and putting value bets into multiples as disguise **[AUTHOR DATA via secondary source]** — [PinnacleOddsDropper summary of Buchdahl](https://www.pinnacleoddsdropper.com/blog/joseph-buchdahl's-betting-strategy)

#### Ed Miller & Matthew Davidow – *The Logic of Sports Betting* (2019, self-published, ISBN 9781096805724)
- The book has three parts: how sportsbooks work, the logic of betting against them, and how to identify good and bad bets. Miller is a poker author with over 300,000 books sold. Davidow is a sports modeller who says he has beaten major markets for more than 15 years and co-founded two private sports analytics firms — [Goodreads](https://www.goodreads.com/book/show/45485081-the-logic-of-sports-betting); [Google Books](https://books.google.com/books/about/The_Logic_of_Sports_Betting.html?id=y_c_xQEACAAJ)
- It explains the "market-making" life-cycle of a line. Early market makers post numbers, a "feeding frenzy" of sharp bettors follows, and further books then post lines with further smaller frenzies. The point is that retail books copy prices from sharper books, which is the structural reason stale soft lines exist **[PRACTITIONER]** — [Mosperity review/summary](https://mosperity.hashnode.dev/the-logic-of-sports-betting-by-ed-miller-and-matthew-davidow-a-comprehensive-review-and-summary); [Yahoo Sports review](https://sports.yahoo.com/books-to-read-why-the-logic-of-sports-betting-is-a-must-have-155411907.html)
- Credibility: widely recommended by practitioners. It is US-centric (NFL/NBA spreads), but the market-structure logic carries over to European football.

#### Stanford Wong – *Sharp Sports Betting* (Pi Yee Press, ISBN 0935926240)
- Described as a "revolutionary text on getting the edge over bookmakers". It covers terminology, odds and methods for bets that should win long-term, and includes NFL push-frequency charts and Poisson odds tables. Reviewers note it is **dated**: internet betting has made markets more efficient — [bj21 review](https://bj21.com/books/sharp-sports-betting-by-stanford-wong); [ThriftBooks](https://www.thriftbooks.com/w/sharp-sports-betting_stanford-wong/339243/)
- Relevance to football: low to medium. It is useful for the maths of converting odds to probabilities and for Poisson thinking, and mostly irrelevant for European football markets.

#### King Yao – *Weighing the Odds in Sports Betting* (Pi Yee Press, ISBN 0935926305)
- Aimed at readers who "want to think analytically" rather than be "spoon-fed supposed winners". It is usually paired with Wong's book as "the two quintessential books for the beginning bettor" — [bj21 article](https://bj21.com/articles/sports-betting/weighing-the-odds-in-sports-betting); [Amazon](https://www.amazon.com/Weighing-Odds-Sports-Betting-King/dp/0935926305)

#### True Pokerjoe – *Sharper: A Guide to Modern Sports Betting* (2016; author updates noted for 2021 and 2024)
- A self-published practitioner guide to "modern" (post-internet, market-based) betting — [Goodreads](https://www.goodreads.com/book/show/31431759-sharper); [2+2 Magazine excerpt](https://www.twoplustwo.com/magazine/issue141/pokerjoe-book-sports-betting.php). Credibility: pseudonymous author, no audited record **[PRACTITIONER]**.

#### Billy Walters – *Gambler: Secrets from a Life at Risk* (Simon & Schuster, August 2023)
- Memoir of a bettor who claims 36 consecutive winning years, with chapters on his strategies. It became a NYT bestseller — [Simon & Schuster](https://www.simonandschuster.com/books/Gambler/Billy-Walters/9781668032862); [Books About Sports list](https://www.booksaboutsports.com/the-best-sports-betting-books.html). Credibility caveat: the track record is self-reported. Walters was also convicted of insider trading (securities, not betting) — [Wikipedia](https://en.wikipedia.org/wiki/Billy_Walters_(gambler)). US sports focus **[PRACTITIONER]**.

#### Sharp-bettor interviews / long-form audio (Rufus Peabody, Spanky)
- *Bet The Process* podcast: Rufus Peabody, a professional bettor known for a statistical approach, with Jeff Ma, formerly of the MIT blackjack team — [Apple Podcasts](https://podcasts.apple.com/us/podcast/bet-the-process/id1291010585). Gadoon "Spanky" Kyrollos left Wall Street to bet professionally and talks about his life as a sharp bettor. Joint Q&A with Peabody, Spanky and Captain Jack — [YouTube](https://www.youtube.com/watch?v=_FJtZRwJSoc). Unabated publishes practitioner education articles — [Unabated education hub](https://unabated.com/content/education). **[PRACTITIONER]**, US-market focused.

#### David Sumpter – *Soccermatics* (2016)
- Chapter 11 covers how bookmakers set spreads. In chapter 12 Sumpter designs his own strategies to beat bookmakers, and chapter 13 tests them — [IMA review](https://ima.org.uk/8996/8996/)
- His "odds-bias" strategy compares bookmaker implied probabilities with observed frequencies. In the English Premier League he identified biases in the pricing of strong home favourites and closely matched draws and made a profit — [Soccermatics odds-bias test on Irish league](https://andrewwoods1.github.io/Odds-Bias-in-Irish-Football/); [We Love Betting review](https://welovebetting.co.uk/book-review-what-can-punters-learn-from-soccermatics/)
- His xG-based model "more or less broke even" against the bookmakers' odds. A reviewer notes the betting experiment was "fairly basic" and ran only a short time, and that post-2016 market changes may have removed the angle **[AUTHOR DATA, small sample]** — [We Love Betting review](https://welovebetting.co.uk/book-review-what-can-punters-learn-from-soccermatics/)
- Follow-up Medium post: "If you had followed the betting advice in Soccermatics you would now be very rich". The content could not be fetched (HTTP 403) — [Medium](https://soccermatics.medium.com/if-you-had-followed-the-betting-advice-in-soccermatics-you-would-now-be-very-rich-1f643a4f5a23)

#### Chris Anderson & David Sally – *The Numbers Game: Why Everything You Know About Soccer Is Wrong* (2013, Penguin)
- Based on more than 8,000 matches (2005–2011) from four top European leagues. It argues corners, possession and managers are overrated and that football is roughly "50/50" skill and luck. A critic notes that the Spiegelhalter study they rely on attributed only about 26% to pure chance — [Tuck School of Business](https://tuck.dartmouth.edu/news/articles/book-seeks-truth-behind-the-statistics-in-soccer); [Penguin Random House](https://www.penguinrandomhouse.com/books/314351/the-numbers-game-by-chris-anderson-and-david-sally/)
- Relevance: modelling priors (high randomness of goals, regression to the mean). It is not a betting-method book.

#### Joe Peta – *Trading Bases* (2013, Dutton/Penguin)
- A former Wall Street trader built an MLB model for the 2011 season that removed "cluster luck" from team statistics, and reported a **41% return** in the first year — [Penguin Random House](https://www.penguinrandomhouse.com/books/311723/trading-bases-by-joe-peta/); [Forbes](https://www.forbes.com/sites/steveschaefer/2013/03/27/betting-on-baseball-the-next-hot-investment-strategy/); [ESPN](https://www.espn.com/blog/playbook/fandom/post/_/id/18799/wall-st-vet-backs-mlb-betting-in-book)
- Credibility: a single-season, self-reported return **[AUTHOR DATA, n = 1 season]**. The lesson that transfers to football is to separate luck, such as clustering of goals or finishing, from underlying performance (xG-type thinking).

#### Nate Silver – *The Signal and the Noise* (2012), chapter 8 "Less and Less and Less Wrong"
- Profiles NBA bettor Bob Voulgaris. The method is Bayesian: aggregate many small signals, update continuously, and bet when your estimate diverges enough from the market. Even a top bettor wins only about 57% of his bets — [Bard College excerpt (PDF)](http://faculty.bard.edu/hhaggard/teaching/sci127Sp20/notes/SilverSignalExcerpt.pdf); [chapter summary](https://www.bookrags.com/studyguide-the-signal-and-the-noise/chapanal008.html) **[PRACTITIONER / journalism]**

#### William Poundstone – *Fortune's Formula* (2005, Hill & Wang)
- The history of the Kelly criterion (John Kelly and Claude Shannon at Bell Labs, 1956) and its use by Ed Thorp in blackjack and in the Princeton-Newport hedge fund. It is mainly about the Kelly criterion, not sports — [Goodreads](https://www.goodreads.com/book/show/186124.Fortune_s_Formula); [Macmillan](https://us.macmillan.com/books/9781250391124/fortunesformula/)

#### Landmark paper: Kaunitz, Zhong & Kreiner (2017), "Beating the bookies with their own numbers – and how the online sports betting market is rigged" (arXiv:1710.02824)
- Method: consensus probability `p_cons = 1/mean(odds across bookmakers)` for each 1X2 outcome (3–32 bookmakers). Bet when `max odds > 1/(p_cons − 0.05)`. Data: 479,440 games from 818 leagues, 2005–2015, 32 bookmakers. Consensus probability was highly calibrated (R² = 0.999 home, 0.995 draw, 0.998 away), with intercepts of −0.034, −0.057 and −0.037 **[EVIDENCE, code and data public]** — [arXiv PDF](https://arxiv.org/pdf/1710.02824); [GitHub code](https://github.com/Lisandro79/BeatTheBookie)
- Results, all pre-tax:
  - Closing-odds backtest: 56,435 bets, 44.4% hit rate, **+3.5%** return; 10.82 SD above a random strategy.
  - Odds 1–5 h pre-kick-off (Sep 2015–Feb 2016, 31,074 games): 6,994 bets, 47.6% hit rate, **+9.9%**.
  - Paper trading: 407 bets, **+5.5%**.
  - Real money ($50 flat stakes, 5 months): 265 bets, **+8.5%**.
  - Paper and real combined: 672 bets, +6.2%, p = 0.089, i.e. *not* significant.
  - About 30% of dashboard odds had already changed at the bookmaker by the time they checked.
  - Bookmakers then "severely limit[ed]" the accounts and required "manual inspection" of bets, and the experiment ended.
  - Source: [arXiv PDF](https://arxiv.org/pdf/1710.02824)

#### Polish-language books (książki po polsku)
- Titles cited by Polish betting portals: *Stań się postrachem bukmacherów* (Bartłomiej Boruta), a beginner-to-intermediate guide; Arkadiusz Kwiatkowski, *Zakłady sportowe i bukmacherskie kontra multilotek*; *Bukmacherski profesjonalizm* (2008), for beginners. The same portal says Polish betting books "mainly contain basic knowledge" and that the English-language literature is much richer — [legalni-bukmacherzy-online.pl](https://www.legalni-bukmacherzy-online.pl/ksiazki-o-zakladach-bukmacherskich/); [Portal Pisarski 2023](https://www.portal-pisarski.pl/news/2023/10/31/ksiazki-o-zakladach-bukmacherskich-%E2%80%93-poradniki-dla-nowych-i-bardziej-zaawansowanych-graczy) **[ANECDOTAL / affiliate]**
- Polish online resources that explain value betting *with the 12% tax*: surebety.pl (valuebet, limits and promotions articles), bukbonus.pl (surebet calculator with tax), and bettingfox.pl (valuebet calculator) — [surebety.pl valuebet](https://surebety.pl/artykul/valuebet/); [BukBonus surebet calculator](https://bukbonus.pl/kalkulatory/surebet); [BettingFox kalkulator](https://bettingfox.pl/warsztat-kalkulator-valuebet/) **[ANECDOTAL / affiliate]**

### Inferences
- The minimum reading list for Model Buk is:
  1. Buchdahl's *Squares & Sharps* and the Wisdom-of-the-Crowd PDF, for sharp-reference value and margin removal.
  2. *Black Cat*, for skill testing.
  3. *Monte Carlo or Bust*, for variance and bankroll planning.
  4. Miller & Davidow, for market structure.
  5. Kaunitz et al., for a replicable blueprint of an hourly scanner.
  6. Uhrín et al. 2021, for staking (see Q3).
  Wong, Yao and Walters are US-sports-centric. Soccermatics, The Numbers Game, Trading Bases and Silver are mostly for modelling intuition.
- None of the books analyses a market with a 12% turnover tax on stakes. Every published yield must be re-derived after tax (see Q2).

### Gaps
- Publication years of some editions could not be confirmed from primary publisher pages in this session: *Black Cat* (believed 2016), *Squares & Sharps* (believed 2016, new edition later), *Monte Carlo or Bust* (believed 2021), *Sharp Sports Betting* (believed 2001) and *Weighing the Odds* (believed 2007). The chapter-level content of Wong and Yao (e.g. teasers, middles, correlated parlays) was not verified.
- Ed Thorp's own books (*Beat the Dealer*, *A Man for All Markets*) and Kelly's 1956 paper were not checked in this session.
- I found no Polish translations of Buchdahl or Miller & Davidow. Whether *The Signal and the Noise* or *Soccermatics* exist in Polish translation was not verified.
- Newer 2020–2026 books, apart from *Gambler* (2023) and *Sharper* updates, surfaced only as low-quality self-published titles (e.g. "Sharp Money vs Public Betting"). I found no well-reviewed, evidence-based 2024–2026 football value-betting book.

---

## Q2. Value-finding methods: how they work, evidence, edge, risks, and viability under the Polish 12% stake tax

### Takeaway
The only method with strong public evidence is **soft-vs-sharp value betting**: compare a soft bookmaker's price with a margin-free Pinnacle or consensus price. It yields roughly **+3% to +10% pre-tax** but gets accounts limited within weeks to months. The Polish 12% stake tax sets the break-even at odds **13.6% above fair**. That turns every published single-bet value strategy negative: for example Buchdahl +3.7% becomes about −8.7%, and Kaunitz's real-money +8.5% becomes about −4.5%. Singles at Polish books are therefore viable only if the model has an edge larger than about 14% (very rare, mostly errors or stale lines), or the tax is refunded by a "bez podatku" promotion. The other route is to combine several genuine value legs into one accumulator, because the tax is charged once per coupon while the edges multiply.

### Cited Findings

#### The Polish tax regime (baseline for every method)
- **Podatek od gier 12%** is deducted from the stake of every coupon at a licensed Polish bookmaker, so only 88% of the stake is actually wagered. Effective odds = listed odds × 0.88 (e.g. 2.00 becomes 1.76). The correct value test is `0.88 · p · k > 1` — [BettingFox valuebet calculator](https://bettingfox.pl/warsztat-kalkulator-valuebet/); [STS blog](https://www.sts.pl/blog/podatek-od-zakladow-bukmacherskich/); [surebety.pl](https://surebety.pl/artykul/podatki-zaklady-bukmacherskie/)
- **Winnings tax:** a single win of up to 2,280 PLN is exempt from income tax. Wins of 2,280 PLN or more are subject to **10% tax on the winnings**, which the bookmaker calculates automatically when the bet is placed — [goal.pl](https://www.goal.pl/poradnik-typera/podatek-u-bukmachera/); [PIT.pl](https://www.pit.pl/aktualnosci/podatek-od-zakladow-bukmacherskich-ile-wynosi-i-kiedy-trzeba-go-zaplacic-1009942)
- Several Polish bookmakers run **"gra bez podatku"** promotions, mostly restricted to accumulators. Betfan: AKO coupons of at least 5 events, each at odds of at least 1.40. Superbet "SuperBonus": at least 8 events, each at odds of at least 1.35. Operators listed as offering tax-free play: Betclic, forBET, LVBET, Superbet, Fortuna, Betters and Betfan. The promotions make wins about 14% higher **[affiliate sources; conditions change often]** — [meczyki.pl (Sept 2026)](https://www.meczyki.pl/bukmacherzy/legalny-bukmacher-bez-podatku-najlepszy-polski-bukmacher/71); [meczyki.pl – Betfan](https://www.meczyki.pl/bukmacherzy/betfan-bez-podatku-jak-skorzystac-z-promocji/114); [bukmacher-legalny.pl](https://www.bukmacher-legalny.pl/bukmacher-bez-podatku/)
- Pinnacle withdrew from the Polish market. Poland keeps a Ministry of Finance register of domains offering gambling illegally (hazard.mf.gov.pl), and ISPs must block listed domains within 48 hours or face fines of up to 250,000 PLN — [hazard.mf.gov.pl](https://hazard.mf.gov.pl/); [Gov.pl](https://www.gov.pl/web/cyfryzacja/rejestr-domen-pomaga-w-walce-z-internetowym-hazardem); [Dudkowiak & Putyra](https://dudkowiak.pl/blog/blokada-stron-hazardowych-w-polsce/)

#### (a) Soft vs sharp value betting (odds comparison against Pinnacle, exchange or consensus)
- **How it works:** estimate the "true" probability from the sharpest price (Pinnacle with the margin removed) or from the average across many bookmakers. Bet any soft bookmaker price above that fair value — [football-data.co.uk WoC](https://www.football-data.co.uk/blog/wisdom_of_the_crowd.php); [Kaunitz et al.](https://arxiv.org/pdf/1710.02824)
- **Evidence:**
  - Buchdahl, 2012/13–2014/15: +3.4% yield on 22,281 bets with no filter, +6.63% with a 2% filter, +13.22% with a 5% filter — [football-data.co.uk](https://www.football-data.co.uk/blog/wisdom_of_the_crowd.php)
  - Buchdahl live tracking from August 2015: about 18,000 value odds, 4.0% expected vs 3.7% actual — [PinnacleOddsDropper summary](https://www.pinnacleoddsdropper.com/blog/joseph-buchdahl's-betting-strategy)
  - Kaunitz: +3.5% (closing), +9.9% (1–5 h pre-match), +8.5% real money over 265 bets — [arXiv](https://arxiv.org/pdf/1710.02824)
- **Why it exists:** bookmakers sometimes price above fair value to attract clients or to balance their books — [Kaunitz et al.](https://arxiv.org/pdf/1710.02824). Retail books copy lines posted by market makers — [Miller & Davidow summary](https://mosperity.hashnode.dev/the-logic-of-sports-betting-by-ed-miller-and-matthew-davidow-a-comprehensive-review-and-summary)
- **Data required:** real-time odds from many bookmakers plus a sharp reference. Kaunitz polled 32 bookmakers from 5 hours before kick-off. About 30% of flagged odds had already moved before a human could place the bet — [arXiv](https://arxiv.org/pdf/1710.02824)
- **Risks:** fast account limitation (Q4), latency and stale-price loss, and margin-removal model error. Buchdahl notes the favourite-longshot bias and several margin-removal methods: equal margin, odds ratio, logarithmic and Shin — [PinnacleOddsDropper CLV/Buchdahl](https://www.pinnacleoddsdropper.com/blog/closing-line-value--clv-demystified-by-expert-joseph-buchdahl)

#### (b) Own probability model (fundamental or statistical model)
- Kaunitz et al. review the literature: expert predictions, Elo, Maher/Dixon-Coles Poisson models, prediction markets. They find "no precedent in the scientific literature" of such models consistently beating the market over years and across leagues — [arXiv](https://arxiv.org/pdf/1710.02824)
- Bookmaker consensus probabilities are already almost perfectly calibrated (R² ≥ 0.995 across 479k games) — [arXiv](https://arxiv.org/pdf/1710.02824)
- Sumpter's xG model roughly broke even against bookmaker odds. His odds-bias angle (draws, strong home favourites) made a small profit over a short test **[AUTHOR DATA]** — [We Love Betting review](https://welovebetting.co.uk/book-review-what-can-punters-learn-from-soccermatics/); [IMA review](https://ima.org.uk/8996/8996/)
- Practitioner view (Voulgaris, via Silver): a model edge comes from aggregating many small signals and betting only when the estimate diverges enough from the line. Even elite bettors hit only about 57% — [Bard excerpt of Silver ch. 8](http://faculty.bard.edu/hhaggard/teaching/sci127Sp20/notes/SilverSignalExcerpt.pdf)

#### (c) Line movement / steam following ("dropping odds")
- **Claimed mechanism:** Pinnacle and other sharp books move first; retail books lag by seconds to minutes; bettors take the stale soft price in that window. Vendors claim a 2–4% edge per bet, compressing to 1–2% by 2026, and that US books detect steam-chasers within 20–50 bets **[MARKETING, vendors sell dropping-odds tools; not independently verified]** — [OddsNotifier](https://oddsnotifier.io/en/blog/dropping-odds-strategy); [OddsCipher](https://oddscipher.com/the-codex/steam/)
- Polish blog: betting just after odds drop is one of the four triggers bookmakers use to detect professionals **[ANECDOTAL]** — [blogbukmacherski.pl](https://blogbukmacherski.pl/limitowanie-i-zamykanie-kont-przez-bukmachera/)
- Kaunitz's delay finding (about 30% of flagged prices already gone) shows how perishable these edges are, even over a scan of a few hours — [arXiv](https://arxiv.org/pdf/1710.02824)

#### (d) Team-news / information edges
- Silver's Voulgaris case: edges come from qualitative information (player tweets, coaching press conferences, positioning stats) combined with data and continuous updating **[PRACTITIONER/journalism]** — [Bard excerpt](http://faculty.bard.edu/hhaggard/teaching/sci127Sp20/notes/SilverSignalExcerpt.pdf)
- No quantitative public evidence on the size or duration of line-up news edges in European football was found (see Gaps).

#### (e) Arbitrage (surebets) and matched betting
- Franck, Verbeek & Nüesch (2013, *Economica* 80(318): 300–325): combining a bookmaker bet with a betting-exchange bet gave a guaranteed positive return in **19.2% of matches** in the top five European leagues. Bookmakers frequently posted arbitrage positions and on average had negative margins on them **[EVIDENCE, older data]** — [Wiley](https://onlinelibrary.wiley.com/doi/10.1111/ecca.12009); [PDF](https://www.wiwi.uni-muenster.de/uf/sites/uf/files/PublikationenNuuesch/2013franck_verbeek_nueesch_2013.pdf)
- In Poland a surebet requires the sum of inverse *effective* odds (odds × 0.88) to be below 1. The 12% tax "znacząco utrudnia" (significantly hinders) surebets, so odds must diverge exceptionally — [ArbiScan](https://arbiscan.pl/blog/surebet-co-to-jest-i-jak-dziala); [BukBonus surebet calculator](https://bukbonus.pl/kalkulatory/surebet)
- Surebet players are the "most harshly limited" group, reduced to about 1–5% of standard limits **[ANECDOTAL]** — [surebety.pl limits](https://surebety.pl/artykul/limity-stawek/)

#### (f) Promotions / bonus exploitation (Polish context)
- Worked example, Fortuna "Bezpieczny zakład": an AKO with combined odds of at least 100 refunds 5× stake × 0.88 in cash if exactly one leg loses (max stake 200 PLN). With an 8% margin per leg, the expected return is **0.925 × stake**, i.e. negative. With zero margin the best case is only **1.012 × stake (+1.2%)**, with enormous variance. The source concludes it is not profitable — [surebety.pl](https://surebety.pl/artykul/bezpieczny-zaklad/)
- Tax-free ("bez podatku") AKO promotions remove the 12% tax on qualifying multi-leg coupons (conditions above) — [meczyki.pl](https://www.meczyki.pl/bukmacherzy/legalny-bukmacher-bez-podatku-najlepszy-polski-bukmacher/71)
- UK regulator data: "betting facilities withdrawal" (which covers bonus exclusion among other things) affected 0.83% of active accounts in 2024 — [UK Gambling Commission](https://www.gamblingcommission.gov.uk/blog/post/commercial-restrictions-by-betting-operators)

#### (g) Niche markets and lower leagues
- The Kaunitz strategy worked across 818 leagues and divisions. Soft bookmakers' mispricing is not confined to top leagues — [arXiv](https://arxiv.org/pdf/1710.02824)
- Vendors claim Pinnacle is less reliable as a reference in niche or regional markets, where some regional books may be sharper, and that futures are inefficient everywhere. Pinnacle's limits and margins are used as "confidence proxies" for how sharp a line is **[MARKETING/PRACTITIONER]** — [PinnacleOddsDropper – limits and vig](https://www.pinnacleoddsdropper.com/guides/market-limits-and-vigs-what-are-they-and-why-are-they-important); [football-data.co.uk – Pinnacle efficiency](https://www.football-data.co.uk/blog/pinnacle_efficiency.php) (primary page rate-limited; could not verify its numbers)
- A search-engine summary claimed that a 14-season backtest of 31,247 EV bets across Europe's 22 main leagues, using Pinnacle pre-closing odds as ground truth, was profitable. I could not identify which page it came from; candidates were the football-data.co.uk Pinnacle-efficiency page and a Trademate Medium post. Treat this claim as **unverified** — [football-data.co.uk – Pinnacle efficiency](https://www.football-data.co.uk/blog/pinnacle_efficiency.php); [Trademate Medium](https://tradematesports.medium.com/closing-line-the-most-important-metric-in-sports-trading-58e56cdb4458)

### Inferences
**1. Break-even under the 12% tax.** EV per PLN paid = `0.88·p·o − 1 = 0.88·(1+E) − 1`, where E is the pre-tax edge vs fair. Break-even requires `o ≥ fair_odds / 0.88 = 1.136 × fair odds`, i.e. listed odds at least 13.6% above the margin-free price.

Example: fair 2.00 (p = 0.50) needs listed odds of at least **2.27** to break even, and at least **2.34** for a net +3% EV.

**2. Published strategies after tax** (post-tax = 0.88 × (1 + yield) − 1):

| Strategy | Pre-tax yield | Post-tax yield |
|---|---|---|
| Kaunitz, closing odds | +3.5% | −8.9% |
| Kaunitz, 1–5 h pre-match | +9.9% | −3.3% |
| Kaunitz, real money | +8.5% | −4.5% |
| Buchdahl WoC, no filter | +3.4% | −9.0% |
| Buchdahl WoC, ≥2% filter | +6.63% | −6.2% |
| Buchdahl WoC, ≥5% filter | +13.22% | −0.4% |
| Buchdahl forward tracking | +3.7% | −8.7% |

Even the most selective bucket in the historical data only just breaks even. **Single-bet soft-vs-sharp value betting at Polish books is structurally unprofitable unless the tax is refunded.**

**3. Accumulators dilute the tax.** The 12% is charged once per coupon while independent edges multiply: net EV = `0.88·∏(1+e_i) − 1`. Break-even number of legs, all at the same Polish book:

| Edge per leg | Legs needed | Check |
|---|---|---|
| 2% | 7 | 1.02⁷ = 1.149 |
| 3% | 5 | 1.03⁵ = 1.159, net ≈ +2.0% |
| 5% | 3 | 1.05³ = 1.158 |

If the payout exceeds 2,280 PLN and the 10% winnings tax applies to the full win, the required product rises to about 1/(0.88 × 0.9) = 1.263. That is about 8 legs at 3% or 5 legs at 5%. It creates a practical stake cap: at 5 legs of odds 2.0 (combined 32), the effective payout passes 2,280 PLN at a stake of about 81 PLN.

The cost is extreme variance. For example, 5 legs at fair odds of about 1.94 each (edge 3% at odds 2.00, true probability about 0.515) hit only about 3.6% of the time.

**4. Tax-free AKO promotions change the picture.** On Betfan (≥5 legs ≥1.40) or Superbet (≥8 legs ≥1.35), value legs combine with no tax: net EV = ∏(1+e_i) − 1. This is probably the single most viable use of a Model Buk value signal at Polish books. It still requires finding enough +EV legs at one Polish book, and surebety.pl assumes margins of about 8% per leg at Polish books.

**5. Hourly scanning matters.** Kaunitz and vendors show value prices disappear within minutes to hours. An hourly scan will miss most stale-line opportunities. Longer-lived edges, such as a model disagreeing with the whole market or early-week lines, suit an hourly or daily cadence better.

**6. Arbitrage and matched betting are essentially dead for a Poland-only operation.** Surebets need about 14% combined divergence after tax, and surebet players are limited fastest.

**7. Own-model approach.** The literature shows beating the closing consensus is very hard. A realistic design for Model Buk is a hybrid:
- use the margin-free sharp or consensus price as the prior;
- let the own model move it only where it has demonstrated CLV;
- require a combined edge well above 13.6%, or route bets into tax-free AKO promotions.

### Gaps
- No primary, quantitative evidence was found for the size or decay of **team-news edges** in European football (e.g. price reaction to line-up announcements about 60 minutes before kick-off).
- No independent (non-vendor) study of **steam-chasing** profitability in football was found. All numbers in (c) are marketing claims.
- Buchdahl's football-data.co.uk article on Pinnacle efficiency (including any league-tier breakdown) could not be read because of HTTP 429.
- The exact tax base of the 10% winnings tax (gross payout vs profit) should be verified in the PIT act. Sources say only "10% od wygranej".
- Whether betting exchanges are legally available to Polish residents, and so whether matched betting is possible at all, was not researched.
- How efficient Polish bookmakers' odds are (margins and how quickly they copy Pinnacle), measured systematically, was not found. The 8% per-leg margin is one blog's assumption.

---

## Q3. Staking: Kelly, fractional Kelly, flat; bankroll and variance guidance

### Takeaway
Full Kelly maximises long-run growth only if the edge is known exactly. With estimated edges it is dangerously volatile. The academic consensus and practitioners recommend **fractional Kelly, typically ¼–½**, or an adaptive or drawdown-constrained variant. Flat staking is a reasonable, robust alternative while an edge is unproven. Under the Polish tax, Kelly must use effective odds of 0.88 × o.

### Cited Findings
- **Kelly background:** John Kelly and Claude Shannon (Bell Labs, 1956) derived the growth-optimal bet size. Ed Thorp applied it to blackjack and later to his hedge fund Princeton-Newport — [Goodreads – Fortune's Formula](https://www.goodreads.com/book/show/186124.Fortune_s_Formula); [Wikipedia – Kelly criterion](https://en.wikipedia.org/wiki/Kelly_criterion)
- Gamblers bet less than full Kelly to reduce the chance of ruin, reduce volatility and allow for model error. Fractional Kelly has smaller drawdowns at the cost of lower growth — [Wikipedia – Kelly criterion](https://en.wikipedia.org/wiki/Kelly_criterion)
- **Uhrín, Šourek, Hubáček & Železný (2021), "Optimal sports betting strategies in practice: an experimental review"** compares Kelly, fractional Kelly, risk-constrained Kelly and modern portfolio theory on horse racing, basketball and **soccer** data. They find extra risk-control methods a "practical necessity", and that an **adaptive variant of fractional Kelly** is "a very suitable choice across a wide range of settings" **[EVIDENCE]** — [arXiv:2107.08827](https://arxiv.org/abs/2107.08827)
- Risk-constrained Kelly (Busseti, Ryu & Boyd) adds a drawdown-probability constraint to Kelly optimisation **[EVIDENCE/theory]** — [arXiv:1603.06183](https://arxiv.org/pdf/1603.06183)
- Harry Crane gives two arguments for fractional Kelly (uncertainty in the edge, and preference) — [Harry Crane Substack](https://harrycrane.substack.com/p/two-arguments-for-fractional-kelly)
- Practitioner summaries: half Kelly achieves about 75% of maximum growth with about 25% of the variance; quarter Kelly about 44% of growth. Full Kelly is too volatile because a model saying 55% may really be 51% or 59% **[PRACTITIONER]** — [OddsCipher Codex – Kelly](https://oddscipher.com/the-codex/kelly/)
- Monte Carlo simulation of a strategy's possible returns and drawdowns is the method Buchdahl teaches for bankroll planning — [Amazon – Monte Carlo or Bust](https://www.amazon.com/Monte-Carlo-Bust-Simulations-Aspiring/dp/0857304852); [Sports Trading Network – modelling your range of returns (Buchdahl, mirrored from Pinnacle)](https://www.sportstradingnetwork.com/article/how-to-model-your-range-of-possible-betting-returns/)
- Kaunitz et al. used **flat $50 stakes** and deliberately chose a threshold (α = 0.05) that gave twice as many bets as α = 0.06 at similar profitability, with a later growing stake in mind — [arXiv](https://arxiv.org/pdf/1710.02824)

### Inferences
- **Kelly with the Polish tax:** `f* = (p·b − (1 − p)) / b`, with `b = 0.88·o − 1`.
  - Example: p = 0.50, o = 2.40 gives b = 1.112 and f* ≈ 5.0% of bankroll at full Kelly. Quarter Kelly is about 1.3%.
  - If `0.88·p·o ≤ 1`, then f* ≤ 0 and there is no bet.
- **Fraction vs growth and variance:** for small edges, betting a fraction c of Kelly gives growth of about c·(2 − c) of the Kelly maximum and variance of about c². So ½ Kelly gives 75% of the growth and 25% of the variance, and ¼ Kelly gives about 44% of the growth and about 6% of the variance. This is consistent with the practitioner figures above.
- **Recommendation for Model Buk:** flat or very small fractional Kelly (≤¼) until at least a few hundred bets show positive CLV (see Q5). Cap stakes per bet and per day. Size each AKO as one bet using its combined probability and odds, with Kelly on the product. Because a leg's edge-estimate error compounds across legs, use an even smaller fraction for AKOs.
- Kaunitz's real-money test (+6.2% over 672 bets, p = 0.089) shows that even a real edge can look statistically insignificant for hundreds of bets. Bankroll plans should assume long flat or negative stretches.

### Gaps
- No source gave concrete drawdown tables (e.g. probability of a 30% or 50% drawdown for ¼ Kelly at a given edge and odds) that could be verified in this session. These should be produced with the project's own Monte Carlo simulation, following Buchdahl.

---

## Q4. Account restrictions ("gubbing"/limitowanie): how fast value bettors get limited; Polish bookmakers

### Takeaway
Restricting winning bettors is standard, legal and well documented. UK regulator data show 4.31% of active accounts restricted, disproportionately profitable ones. Researchers were cut to stakes of a few dollars within weeks to months. For Polish bookmakers (STS, Fortuna, Betclic, Superbet, LV BET) there is **no systematic data**, only consistent anecdotal reports that winners, surebettors and value bettors are limited to tens of PLN or less. Any Model Buk plan must assume single-account lifetimes of weeks to months at meaningful stakes.

### Cited Findings

#### International evidence
- **UK Gambling Commission, data for calendar year 2024 (published 23 July 2025):**
  - 643,779 of 14,923,840 active accounts (**4.31%**) had commercial restrictions.
  - Stake factoring affected 2.68% of all accounts (62.17% of restricted ones); commercial closures 2.23%; betting-facilities withdrawal 0.83%; market limitation 0.25%.
  - Among stake-factored accounts, **36.22% were limited to 1–9%** of normal stakes and **22.41% to less than 1%**.
  - **46.78% of restricted customers were in profit**, vs 25.42% of all active customers.
  - The Commission says it is not within its remit to dictate how operators manage commercial liabilities.
  - **[EVIDENCE, regulator data]** — [UK Gambling Commission blog](https://www.gamblingcommission.gov.uk/blog/post/commercial-restrictions-by-betting-operators); [Racing Post analysis](https://www.racingpost.com/news/britain/staking-restrictions-and-account-closures-inside-the-gambling-commissions-latest-betting-data-a5WCz2R9nPyW/)
- **Kaunitz et al.:** bookmakers began "to severely limit our accounts" after real-money betting started. The introduction says "a few weeks after"; the results section says "a few months after". Bets were limited or sent for manual inspection, and most $50 bets were refused or cut. They report (Fig. 4, as summarised by secondary sources) offered maximum stakes such as Interwetten $11.11, Sportingbet $1.25 and Betway $10.45 **[EVIDENCE]** — [arXiv PDF](https://arxiv.org/pdf/1710.02824); [Sports Handle coverage](https://sportshandle.com/sportsbooks-vs-academics-one-wins-battle/)
- Buchdahl: repeatedly taking superior prices means "you can often expect to have your betting activity curtailed" — [football-data.co.uk](https://www.football-data.co.uk/blog/wisdom_of_the_crowd.php). He also says using the Pinnacle method "will see your betting account restricted too" and suggests small stakes and multiples as disguise — [PinnacleOddsDropper summary](https://www.pinnacleoddsdropper.com/blog/joseph-buchdahl's-betting-strategy)
- Vendor claims: aggressive bettors with high stakes and clear positive CLV are limited within 2–4 weeks at soft books, while cautious bettors keep accounts for 3–12 months. Triggers are beating the closing line, odd stake amounts and reacting quickly to line moves **[MARKETING, figures unverified; seen in search snippets from these vendor/affiliate pages]** — [lytic.bet](https://lytic.bet/guide/bookmaker-restrictions); [RebelBetting blog](https://www.rebelbetting.com/blog/how-to-avoid-bookmaker-limitations); [DeucesCracked](https://www.deucescracked.com/blog/why-sportsbooks-limit-winning-bettors-2026)

#### Polish bookmakers (all anecdotal)
- blogbukmacherski.pl names **STS, Fortuna and Betclic** as popular places where limits are enforced. The same article also recommends **Betfan and Betclic** as operators that "relatively rarely" block accounts, which contradicts itself on Betclic. It lists four triggers: profitability; unusual stake amounts (e.g. 139.87 PLN, typical of surebets); short sessions (log in, bet, leave); and betting after odds fall. It knows players limited to **$2 per bet with manual authorisation** at large international books. Legal basis: every regulamin allows the bookmaker to refuse a bet or close an account without giving a reason **[ANECDOTAL]** — [blogbukmacherski.pl](https://blogbukmacherski.pl/limitowanie-i-zamykanie-kont-przez-bukmachera/)
- surebety.pl, covering STS, Fortuna, Betclic, Superbet, LVbet, Betfan, Totalbet, eToto and PZBUK:
  - "Najostrzej limitowaną grupą są gracze surebetów" (surebet players are the most harshly limited group), cut to about 1–5% of standard limits.
  - Post-limit tiers: "spalone konto" (burned account) 0–5 € per bet; low 0–50 €; medium 50–200 €.
  - STS's rules forbid using "pewniaki" (sure-thing bets) to get around limits, on pain of bet cancellation.
  - **[ANECDOTAL]** — [surebety.pl – limity stawek](https://surebety.pl/artykul/limity-stawek/)
- Polish affiliate guides say excluding winning players through closures or drastic limits is a "widespread though unofficial practice", with limits as low as tens of PLN. Published maximum payouts per coupon: STS 1,000,000 PLN; Betclic 800,000; Superbet 650,000; Fortuna 601,000. Unverified accounts are capped at about 5,000 PLN in deposits until verification **[affiliate]** — [goal.pl – limity](https://www.goal.pl/poradnik-typera/limity-u-bukmacherow/); [meczyki.pl – limity](https://www.meczyki.pl/bukmacherzy/limity-u-bukmacherow/115); [legalsport.pl](https://www.legalsport.pl/vademecum/poradnik-gracza/limity-bukmacherskie-co-to-takiego/)

### Inferences
- With KYC-verified Polish accounts, one account per person per operator and PESEL-linked identity, multi-accounting is not a legal or practical option. The pool of Polish licensed operators (about 10–20) is the hard ceiling on total capacity. Once the best-priced books (the ones most often showing "value") limit an account, a soft-vs-sharp strategy loses exactly the prices it relies on. Kaunitz's point about non-random sampling bias applies.
- Design implications for Model Buk:
  - Output a few high-confidence bets rather than every edge.
  - Avoid odd stake amounts.
  - Avoid systematically betting just after sharp moves.
  - Consider AKO structures, which are also less suspicious.
  - Track each account's stake-limit history as a data field.
- The UK figures (46.8% of restricted accounts in profit vs 25.4% overall) mean that about half of restricted accounts are *losing*. Restrictions also target bonus abuse and other behaviour, not only skill.

### Gaps
- **No systematic data on Polish bookmakers' restriction rates**, time-to-limit, or post-limit stake levels (STS, Fortuna, Betclic, Superbet, LV BET). No Polish regulator (Ministerstwo Finansów) statistics comparable to the UK Gambling Commission's were found.
- LV BET's current (2026) status in the Polish sports-betting market was not verified.
- Polish forum threads (e.g. forum.bukmacherskie.com) were not systematically reviewed. Only one valuebetting thread surfaced — [forum.bukmacherskie.com](https://forum.bukmacherskie.com/threads/tylko-valuebetting-tylko-najwy%C5%BCsze-kursy-trzymamy-si%C4%99-zasad.159373/)

---

## Q5. How to verify a tipster or model: CLV, sample size, t-tests (Buchdahl / Pinnacle)

### Takeaway
Profit alone needs **thousands of bets** to separate a 2–5% edge from luck. **Closing line value** (bet odds ÷ margin-free Pinnacle closing odds) has about one tenth of the per-bet noise. It can show skill in about **50–65 bets** in Buchdahl's examples, which makes it the primary KPI for Model Buk's validation. The standard test is a one-sample t-test of yield against zero, with a standard deviation that depends on the odds.

### Cited Findings
- Buchdahl on CLV:
  - CLV in decimal odds is "the sportsbook's odds you have bet divided by the closing odds". Pinnacle's margin must first be removed (equal margin, odds ratio, logarithmic or Shin methods).
  - Pinnacle's closing line is "widely accepted as providing the most accurate numbers".
  - For even-money bets with 5% EV, profit/loss could need "several thousand bets" to rule out chance, but CLV could need "as few as just 50". The per-bet standard deviation is about 1.00 for profit vs about 0.1 for CLV.
  - In one test, 952 positive-expectation bets showed price movements that were "effectively impossible" by chance, and "only 65 bets" would have been needed.
  - Actual profit/turnover of 3.4% vs expected 4.0% over nearly 20,000 bets was consistent with expectation.
  - **[AUTHOR DATA via secondary summary; Pinnacle originals unreachable]** — [PinnacleOddsDropper – CLV demystified by Buchdahl](https://www.pinnacleoddsdropper.com/blog/closing-line-value--clv-demystified-by-expert-joseph-buchdahl); original Pinnacle article: [Using the closing line to test betting skill](https://www.pinnacle.com/en/betting-articles/betting-strategy/using-closing-line-to-test-betting-skill/7e6jwjm5ykejuwkq)
- On t-tests, Buchdahl says the t-test gives the likelihood that a profit could have arisen by chance. For more than about 30 wagers the t-distribution is effectively normal. For a system with 1.72% mean EV, he would need "many thousands of wagers" to show meaningful statistical evidence of skill from profit alone — [search snippet of Pinnacle/Buchdahl material](https://www.pinnacle.com/en/betting-articles/betting-strategy/using-closing-line-to-test-betting-skill/7e6jwjm5ykejuwkq)
- Buchdahl's t-score is converted to a p-value with Excel's TDIST, using degrees of freedom equal to the number of wagers minus one. For break-even betting, the variance of returns is proportional to the odds minus 1 — [Sports Trading Network / Pinnacle "How to model your range of possible betting returns"](https://www.sportstradingnetwork.com/article/how-to-model-your-range-of-possible-betting-returns/); [football-data.co.uk – Luck vs Skill in Sports Betting](https://www.football-data.co.uk/blog/luck_skill_sports_betting.php) (primary page rate-limited during this session)
- Pinnacle also has a Bayes-factor approach to testing betting skill — [Pinnacle – Bayes factor part two](https://www.pinnacle.com/en/betting-articles/educational/bayes-factor-betting-skill-part-two/NF52L4MWXXV7785G). A tipster-evaluation article exists too — [Pinnacle – How good are betting tipsters?](https://www.pinnacle.com/en/betting-articles/Betting-Strategy/how-good-are-betting-tipsters/LP923GAHKWH7NV6A) (neither could be fetched)
- Pyckio, a tipster platform, uses Pinnacle closing odds to judge tipster skill — [Pyckio blog](https://blog.pyckio.com/en/eg-pinnacle-closing-odds/)
- Real-world illustration: Kaunitz's combined 672 bets at +6.2% had p = 0.089. Their 56,435-bet backtest at +3.5% was 10.8 SD above random — [arXiv](https://arxiv.org/pdf/1710.02824)

### Inferences
- **t-test for yield (derivation).** For a bet at decimal odds o with true yield r, the win probability is p = (1+r)/o. The per-bet variance of return is `(1+r)(o − 1 − r)`. So:
  - `t ≈ r·√n / √((1+r)(o − 1 − r))`
  - required `n ≈ (t/r)² · (1+r)(o − 1 − r)`
  This matches Buchdahl's statement that variance scales with odds − 1.
- **Worked sample sizes** (t = 2, one-sided p ≈ 0.023):

| Yield r | Odds o | Bets needed |
|---|---|---|
| 3% | 2.0 | ≈ 4,400 |
| 3% | 3.0 | ≈ 9,000 |
| 5% | 2.0 | ≈ 1,600 |

  For Model Buk, r is the **post-tax** yield: compute P&L on the full stake including the 12% deduction.
- **Validation protocol for Model Buk:**
  1. Log each bet's taken odds, time, bookmaker, stake and tax.
  2. Log the Pinnacle (or sharp-consensus) closing odds with the margin removed, using the same method as the model, e.g. Shin or power.
  3. Compute CLV% = taken odds / fair closing odds − 1. Also compute *post-tax CLV* = 0.88 × taken odds / fair closing odds − 1, which must be positive for the bet to have been +EV.
  4. Run a t-test on CLV (fast signal) and on post-tax P&L (slow confirmation).
  5. Keep Monte Carlo confidence bands of expected P&L given the logged EVs, following Buchdahl.
- Beware multiple testing. Scanning 20 leagues × many markets × many thresholds inflates false positives, so thresholds must be fixed before out-of-sample evaluation.

### Gaps
- Buchdahl's exact original formula text (football-data.co.uk "Luck versus Skill" and the Pinnacle originals) could not be read directly in this session: Pinnacle was unreachable and football-data.co.uk returned HTTP 429. The formula above is a standard derivation consistent with his statements and should be checked against the original.
- Access to Pinnacle closing odds from Poland (the website appears blocked) will need a data vendor or API, or the Pinnacle closing columns in football-data.co.uk CSVs for historical work. The licensing and ToS implications were not researched.
