# Minimum Wage Increases and Youth Employment: Evidence from a Staggered County-Level Difference-in-Differences Design

E. Castellanos, W. Broughton, F. Adeyemi — Center for Labor Market Analysis (working paper, not peer reviewed)

## Abstract

We estimate the employment effect of county-level minimum wage increases enacted between 2012 and 2022 using a staggered difference-in-differences design with heterogeneity-robust estimators. Across 214 treated counties and 1,880 never-treated or not-yet-treated comparison counties, a 10% increase in the effective minimum wage is associated with a 0.42% decline in employment among workers aged 16–24 in affected industries (95% CI −1.13% to 0.29%), an estimate statistically indistinguishable from zero. Hours per worker decline by 1.1% (95% CI −1.9% to −0.3%). We conclude that in the range of increases observed in this period, the adjustment margin is hours rather than headcount.

## 1. Introduction

The employment effect of minimum wage increases is among the most studied questions in applied labor economics, and estimates remain dispersed. Early cross-state comparisons reported large disemployment effects; border-discontinuity designs generally reported effects near zero; recent bunching estimators have reported small negative effects concentrated in specific sectors.

Two methodological developments motivate a re-estimation. First, the staggered adoption of local minimum wage ordinances creates a design in which the conventional two-way fixed effects estimator uses already-treated units as controls, producing weights that can be negative and estimates that are not interpretable as an average treatment effect. Second, the availability of county-by-industry quarterly employment and hours data allows separation of the headcount and hours margins, which are conflated in headcount-only outcomes.

## 2. Data

We use the Quarterly Census of Employment and Wages county-by-industry panel for 2010Q1 through 2023Q4, restricted to the accommodation and food services, retail trade, and administrative support sectors, which together account for the large majority of minimum-wage employment. Employment is measured as the average monthly employment count; hours are constructed from the linked establishment hours file.

Treatment is defined as a county experiencing an increase in the binding effective minimum wage of at least 5% in real terms, where the effective minimum is the maximum of the federal, state, and local statutory minimum. We identify 214 such counties with a first treatment between 2012Q1 and 2022Q2. Counties whose effective minimum changed only through federal or state indexation are classified as untreated.

Comparison counties are never-treated counties and not-yet-treated counties, restricted to counties in the same census division as at least one treated county to reduce geographic dissimilarity.

## 3. Empirical strategy

We estimate group-time average treatment effects using the Callaway–Sant'Anna estimator with doubly robust adjustment, aggregated into an event-study and an overall average effect. Covariates used in the propensity and outcome models are pre-period county population, share of employment in the three treated sectors, median household income, and census division fixed effects.

The identifying assumption is conditional parallel trends: absent treatment, treated and comparison counties would have followed the same employment path conditional on the covariates. We assess this with pre-treatment event-study coefficients over eight quarters and with the Rambachan–Roth sensitivity analysis, which reports the largest violation of parallel trends under which the sign of the estimate would be preserved.

Standard errors are clustered at the county level, with a wild bootstrap given the moderate number of treated clusters.

## 4. Results

Pre-treatment event-study coefficients over the eight quarters before treatment are individually and jointly indistinguishable from zero (joint p = 0.41), with point estimates between −0.28% and 0.31%.

The aggregated overall effect on youth employment in treated sectors is −0.42% per 10% minimum wage increase, with a 95% confidence interval of −1.13% to 0.29%. The event-study path shows no discernible trend break at treatment and no accumulation of negative effects over the twelve post-treatment quarters we observe.

The effect on average weekly hours per worker is −1.1% per 10% increase, with a 95% confidence interval of −1.9% to −0.3%. The hours effect emerges within three quarters of treatment and is stable thereafter. Total wage bill in treated sectors increases by 2.9% (95% CI 1.4% to 4.4%).

Rambachan–Roth sensitivity indicates that the hours result retains its sign under violations of parallel trends up to 1.4 times the maximum observed pre-period violation, while the employment result is not sign-stable under any nonzero violation, as expected for an estimate whose confidence interval contains zero.

Splitting by increase size, counties with increases above 15% show an employment estimate of −0.9% (95% CI −2.2% to 0.4%), which is more negative than the pooled estimate but still not distinguishable from zero at conventional levels.

## 5. Discussion

The results are consistent with an adjustment margin that operates through scheduled hours rather than through separations, at least over the range of increases and the horizon observed here. This interpretation is supported by the coincidence of a precisely estimated hours decline with an imprecisely estimated headcount change and an increase in the total wage bill.

The estimates are bounded in ways that matter for policy use. The observed increases are mostly in the 5–20% range; nothing here speaks to increases substantially outside that range, and the larger-increase subgroup is the point at which the estimate begins to move. The horizon is twelve quarters, so slow-moving adjustment through establishment entry and exit, or through capital substitution, is only partially captured. The sample is restricted to three sectors and to counties in divisions containing at least one treated county, which limits external validity to labor markets resembling those.

The employment estimate is a null result with a confidence interval that includes economically meaningful negative values at its lower bound. We therefore describe it as not distinguishable from zero rather than as evidence of no effect. The hours result is the estimate that carries statistical weight.

## 6. Conclusion

County-level minimum wage increases of 5–20% in the 2012–2022 period are associated with a precisely estimated decline in hours per worker and an imprecisely estimated change in youth headcount employment. Extending the horizon beyond three years and covering larger increases are the two extensions most likely to change the conclusion.

## Acknowledgments

We thank seminar participants at three university workshops for comments on the identification strategy, and the state labor departments that provided ordinance effective-date documentation.

## References

1. Card D, Krueger A. Minimum wages and employment: a case study of the fast-food industry. American Economic Review, 1994.
2. Callaway B, Sant'Anna P. Difference-in-differences with multiple time periods. Journal of Econometrics, 2021.
3. Goodman-Bacon A. Difference-in-differences with variation in treatment timing. Journal of Econometrics, 2021.
4. Rambachan A, Roth J. A more credible approach to parallel trends. Review of Economic Studies, 2023.
5. Cengiz D, Dube A, Lindner A, Zipperer B. The effect of minimum wages on low-wage jobs. Quarterly Journal of Economics, 2019.
6. Neumark D, Wascher W. Minimum wages and employment. Foundations and Trends in Microeconomics, 2007.
7. Dube A, Lester T, Reich M. Minimum wage effects across state borders. Review of Economics and Statistics, 2010.
8. Broughton W, Adeyemi F. Hours versus headcount adjustment in low-wage labor markets. Working paper, 2023.
