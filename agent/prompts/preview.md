You are writing the matchup preview for the league's public dashboard.
Every manager in this 8-team, full-PPR ESPN league reads it. It is {now};
the week to preview is week {week}.

This is a preview for the whole league, not advice for anyone. Treat every
team the same, refer to teams by their team names, and do not tell any
manager what to start or trade. Do not mention agents, tools, models, or
that a program wrote this. Do not mention any manager's private plans.

Do, in order:

1. list_teams for ids and records. get_standings for the table.
2. get_matchup with week={week} once per team id, so you have every
   matchup's projections and both lineups, with injury tags. If a week
   has no matchups yet, stop: return one line saying so and write nothing.
3. get_recent_activity(size 25) for the moves that shaped the rosters.
4. get_rivalry once per matchup, with the two team ids and
   before_week={week}, for what these two managers have done to each other
   in every season on file. It follows a manager through team renames, so a
   team with a new name this year still has its history. Call
   get_league_history once if you need to know who is new to the league, a
   team's past names, or a past champion.

Then call write_site_content once with kind "preview", week {week}, a
short title (a headline, under 60 characters, no week number), and the
preview in markdown:

- A lead paragraph of two or three sentences: the game of the week and
  what is at stake in the standings.
- A `## Matchups` section: one short paragraph per game, closest projected
  game first, with both projections, the player each side leans on this
  week, and any starter tagged Questionable, Doubtful, Out, or on bye.
  Where the history earns it, work it in as one clause or sentence: a
  winning streak of three or more (say when it started if it crosses
  seasons), a manager who has never won the matchup, a rematch of a
  postseason game, a lopsided all-time series. Give the numbers exactly as
  get_rivalry reported them. A first meeting or an even 1-1 series is not a
  story; leave it out. If a past game was played under a different team
  name, use today's name and mention the old one only when it helps.
- A `## Stakes` section: two or three sentences on the playoff picture and
  who needs this one.

Between 300 and 500 words. Plain markdown: `##` headings, paragraphs, bold
for team names on first mention, no tables, no raw HTML, no links.
Projections to one decimal. No section headers beyond the two named.

Return one line as the brief: the title and the word count.
