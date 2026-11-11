#!/usr/bin/env python3
"""Rebuild README.md from the public pages of hotskills.dev.

Reads only pages any visitor can open (leaderboard, rising, new, categories), never a data API.
Standard library only. Exits with an error, writing nothing, when a page looks wrong, so the
previous README stays online.
"""
import argparse
import datetime
import re
import sys
import time
import unicodedata
import urllib.request
from html.parser import HTMLParser

BASE = 'https://hotskills.dev'
USER_AGENT = 'hotskills-awesome-list/1.0 (+https://github.com/hotskillsdev/awesome-agent-skills)'

# Only the categories whose top entries read correctly today. Sales, operations, writing, education, productivity,
# data, devops, security, testing and docs come back when the category fix (CATEGORY-PRECISION) is online.
FOR_THE_TEAM = [('marketing', 'Marketing'), ('legal', 'Legal'), ('finance', 'Finance'), ('research', 'Research')]
FOR_BUILDERS = [('engineering', 'Engineering'), ('design', 'Design')]

PER_CATEGORY = 5
MIN_PER_CATEGORY = 3
MIN_STARS_NEW = 100

REPO_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+$')
SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9._-]*$')
STARS_RE = re.compile(r'^[0-9][0-9.,]*[kM]?$')
GROWTH_RE = re.compile(r'^([+\-−]?[0-9][0-9.,]*%)')
URL_RE = re.compile(r'(https?://|www\.)\S+', re.I)
MD_SPECIAL_RE = re.compile(r'([\\`*_{}\[\]<>|#!~])')


class RowParser(HTMLParser):
    """Collects the leaderboard rows: <li class="row" data-repo=…> with repo, description, growth and stars."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.row = None
        self.depth = 0
        self.field = None
        self.field_tag = None
        self.next_span = None
        self.next_b = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = (attrs.get('class') or '').split()
        if self.row is None:
            if tag == 'li' and 'row' in classes and attrs.get('data-repo'):
                self.row = {'slug': attrs['data-repo'], 'categories': (attrs.get('data-categories') or '').split(),
                            'repo': '', 'desc': '', 'growth': '', 'stars': ''}
                self.depth = 1
            return
        if tag == 'li':
            self.depth += 1
        if tag == 'div' and 'momentum' in classes:
            self.next_span = 'growth'
        elif tag == 'div' and 'stars' in classes:
            self.next_b = 'stars'
        if self.field is None:
            if tag == 'a' and 'repo' in classes:
                self.field, self.field_tag = 'repo', 'a'
            elif tag == 'p' and 'desc' in classes:
                self.field, self.field_tag = 'desc', 'p'
            elif tag == 'span' and self.next_span:
                self.field, self.field_tag, self.next_span = self.next_span, 'span', None
            elif tag == 'b' and self.next_b:
                self.field, self.field_tag, self.next_b = self.next_b, 'b', None

    def handle_endtag(self, tag):
        if self.row is None:
            return
        if self.field is not None and tag == self.field_tag:
            self.field = self.field_tag = None
        if tag == 'li':
            self.depth -= 1
            if self.depth == 0:
                self.rows.append(self.row)
                self.row = None

    def handle_data(self, data):
        if self.row is not None and self.field is not None:
            self.row[self.field] += data


def parse_rows(page):
    """Valid rows only: a row whose repo, slug or stars do not match the expected shape is dropped."""
    parser = RowParser()
    parser.feed(page)
    rows = []
    for row in parser.rows:
        repo = row['repo'].strip()
        stars = row['stars'].strip()
        growth = GROWTH_RE.match(row['growth'].strip())
        if not (REPO_RE.match(repo) and SLUG_RE.match(row['slug']) and STARS_RE.match(stars)):
            continue
        rows.append({'repo': repo, 'slug': row['slug'], 'categories': row['categories'], 'desc': row['desc'],
                     'stars': stars, 'growth': growth.group(1).replace('−', '-') if growth else ''})
    return rows


def stars_value(text):
    number = float(text.rstrip('kM').replace(',', ''))
    return number * (1000 if text.endswith('k') else 1000000 if text.endswith('M') else 1)


def tracked_count(page):
    match = re.search(r'([0-9][0-9.,]*k?)\s+repositories tracked', re.sub(r'<[^>]+>', ' ', page))
    return match.group(1) if match else ''


def width(ch):
    return 2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1


def clean_description(text, limit=110):
    """Author text made safe for a Markdown table: no control or invisible characters, no links, short, escaped."""
    text = ''.join(ch for ch in text if unicodedata.category(ch) not in ('Cc', 'Cf', 'Co', 'Cs', 'Cn'))
    text = URL_RE.sub('', text)
    text = re.sub(r'\s+', ' ', text).strip()
    used, out = 0, []
    for ch in text:
        if used + width(ch) > limit - 1:
            cut = ''.join(out)
            if ' ' in cut[len(cut) // 2:]:
                cut = cut.rsplit(' ', 1)[0]
            text = cut.rstrip(' ,.;:-–—') + '…'
            break
        out.append(ch)
        used += width(ch)
    return MD_SPECIAL_RE.sub(r'\\\1', text)


def skill_link(row):
    return '[%s](%s/skill/%s/)' % (MD_SPECIAL_RE.sub(r'\\\1', row['repo']), BASE, row['slug'])


def github_link(row):
    return '[GitHub](https://github.com/%s)' % row['repo']


def table(rows, growth_title, numbered=True):
    head = ('| # ' if numbered else '') + '| Skill | What it does | Stars | %s |' % growth_title
    rule = ('|--:' if numbered else '') + '|---|---|--:|--:|'
    lines = [head, rule]
    for index, row in enumerate(rows, 1):
        cells = [skill_link(row) + ' <sub>' + github_link(row) + '</sub>', clean_description(row['desc']),
                 row['stars'], row['growth'] or '—']
        lines.append(('| %d ' % index if numbered else '') + '| ' + ' | '.join(cells) + ' |')
    return '\n'.join(lines)


def bullets(rows):
    lines = []
    for row in rows:
        desc = clean_description(row['desc'])
        lines.append('- %s%s · ★ %s · %s' % (skill_link(row), ' — ' + desc if desc else '',
                                                            row['stars'], github_link(row)))
    return '\n'.join(lines)


def primary(rows, slug):
    """Repositories whose first (strongest) category is this one, so a category is not filled by generalists."""
    return [row for row in rows if row['categories'][:1] == [slug]][:PER_CATEGORY]


def render(data, today):
    tracked = data['tracked']
    date = '%s %d, %d' % (today.strftime('%b'), today.day, today.year)
    intro = ('Agent skills (`SKILL.md`) for Claude Code, Codex, Cursor and other agents, ranked by real GitHub '
             'growth. Rebuilt every day from [HotSkills.dev](%s/)%s.'
             % (BASE, ', which checks %s skill repositories every hour' % tracked if tracked else ''))
    out = ['# Awesome Agent Skills: ranked by real growth on [HotSkills.dev](%s/)' % BASE, '', intro, '',
           '**Last update: %s** · [Live leaderboard](%s/) · [Rising now](%s/rising/) · '
           '[Hot Monday newsletter](%s/newsletter/)' % (date, BASE, BASE, BASE), '',
           '## Contents', '',
           '- [Rising this week](#rising-this-week)', '- [Most starred](#most-starred)',
           '- [New this month](#new-this-month)', '- [For the rest of the team](#for-the-rest-of-the-team)',
           '- [For builders](#for-builders)', '- [Before you install](#before-you-install)',
           '- [No pull requests: use your agent](#no-pull-requests-use-your-agent)', '- [How this list is made](#how-this-list-is-made)',
           '', '## Rising this week', '',
           'Repositories with at least 500 GitHub stars, ranked by star growth over the last 7 days. '
           '[Hourly version](%s/rising/).' % BASE, '', table(data['rising'], '7 days'), '',
           '## Most starred', '', 'The largest agent skill repositories by GitHub stars. [Full leaderboard](%s/).' % BASE,
           '', table(data['top'], '7 days'), '',
           '## New this month', '',
           'Created in the last 30 days, at least %d stars, ordered by 24-hour growth. '
           '[All new skills](%s/new/).' % (MIN_STARS_NEW, BASE), '',
           table(data['new'], '24 hours', numbered=False), '',
           '## For the rest of the team', '',
           'Skills for marketing, legal, finance and research work, by GitHub stars.', '']
    for slug, name in FOR_THE_TEAM:
        if slug in data['categories']:
            out += ['### %s' % name, '', bullets(data['categories'][slug]), '',
                    '[More %s skills](%s/category/%s/)' % (name.lower(), BASE, slug), '']
    out += ['## For builders', '']
    for slug, name in FOR_BUILDERS:
        if slug in data['categories']:
            out += ['### %s' % name, '', bullets(data['categories'][slug]), '',
                    '[More %s skills](%s/category/%s/)' % (name.lower(), BASE, slug), '']
    out += ['## Before you install', '',
            'Every skill page on hotskills.dev shows what the skill loads into your agent\'s context, which files '
            'it runs, and what changed in its `SKILL.md` week by week. Security signals are automated, not a '
            'guarantee: read what a skill runs before you install it.', '',
            '## No pull requests: use your agent', '',
            'Other lists grow through pull requests. This one rebuilds itself every day from HotSkills.dev, so a '
            'pull request cannot add, move or remove a skill. Connect the hotskills MCP server instead, and ask '
            'your agent to:', '',
            '- find skills for a task and compare their growth (`search_skills`, `trending`);',
            '- read what a skill runs before you install it (`security_report`);',
            '- get an install command pinned to the checked commit (`install_command`);',
            '- vote for this week\'s Hot pick (`vote_hot`: one vote per GitHub account, only after you confirm).', '',
            'The key is free and personal: [set it up](%s/mcp/). A skill is missing? '
            '[Suggest it](%s/suggest/).' % (BASE, BASE), '',
            '## How this list is made', '',
            '- Data comes from the GitHub API through hotskills.dev, which refreshes stars every hour. A GitHub '
            'Action rebuilds this README every day from the public pages.',
            '- Inclusion rules are in the [methodology](%s/methodology/). Skills are found automatically; '
            '[suggest one](%s/suggest/) if it is missing.' % (BASE, BASE),
            '- No paid placements. Sponsored content never appears in this list and never changes a ranking.',
            '- Descriptions are written by repository authors. We shorten them and remove links; treat them as '
            'the authors\' claims.',
            '- Are you an author? Your skill page has a README badge and a "Claim this skill" button.', '',
            '## License', '',
            'List text: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), hotskills.dev. Repository '
            'names and descriptions belong to their authors.', '']
    return '\n'.join(out)


def fetch(path, attempts=3):
    request = urllib.request.Request(BASE + path, headers={'User-Agent': USER_AGENT})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read().decode('utf-8')
        except OSError:
            if attempt == attempts - 1:
                raise
            time.sleep(5 * (attempt + 1))


def collect(fetcher=fetch, pause=0.5):
    home = fetcher('/')
    data = {'tracked': tracked_count(home), 'top': parse_rows(home)[:25]}
    data['rising'] = parse_rows(fetcher('/rising/'))[:10]
    data['new'] = [row for row in parse_rows(fetcher('/new/')) if stars_value(row['stars']) >= MIN_STARS_NEW][:10]
    data['categories'] = {}
    for slug, _ in FOR_THE_TEAM + FOR_BUILDERS:
        time.sleep(pause)
        rows = primary(parse_rows(fetcher('/category/%s/' % slug)), slug)
        if len(rows) >= MIN_PER_CATEGORY:
            data['categories'][slug] = rows
    return data


def check(data):
    """The pages must look like a real leaderboard; anything thinner means the site changed or failed."""
    problems = []
    if len(data['top']) < 20:
        problems.append('leaderboard: %d rows' % len(data['top']))
    if len(data['rising']) < 5:
        problems.append('rising: %d rows' % len(data['rising']))
    if len(data['new']) < 5:
        problems.append('new: %d rows' % len(data['new']))
    if len(data['categories']) < 4:
        problems.append('categories with %d+ entries: %d' % (MIN_PER_CATEGORY, len(data['categories'])))
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--out', default='README.md')
    args = parser.parse_args(argv)
    data = collect()
    problems = check(data)
    if problems:
        print('Not updated, the pages look wrong: ' + '; '.join(problems), file=sys.stderr)
        return 1
    text = render(data, datetime.datetime.now(datetime.timezone.utc).date())
    with open(args.out, 'w', encoding='utf-8') as handle:
        handle.write(text)
    print('Wrote %s: %d rising, %d top, %d new, %d categories'
          % (args.out, len(data['rising']), len(data['top']), len(data['new']), len(data['categories'])))
    return 0


if __name__ == '__main__':
    sys.exit(main())
