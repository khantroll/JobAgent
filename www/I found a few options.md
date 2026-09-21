I found a few options ? which would you like?
????
?
You said: Q: What kind of job search agent are you looking to build?
Q: What kind of job search agent are you looking to build? A: Both ? help me plan the full system
Q: Which parts do you want to automate? (Select all that apply) A: Search & discover job listings, Auto-fill & submit applications, Filter & rank jobs by fit, Generate tailored resumes/cover letters
May 19
??
??
Claude responded: Great ? you want the full pipeline.
Great ? you want the full pipeline. Let me lay out a practical architecture for this system.
V
visualize
V
visualize show_widget
Here's the full system architecture. Each stage is clickable for more detail. Here's how it breaks down practically:
What you build vs what you connect
The system has two layers: an AI brain (Claude API) and a web automation layer (browser agent). They serve different jobs and need to be built separately.
Stage 1 ? Profile setup (one-time): You store a structured profile: your base resume, target titles, skills, location preferences, salary range, and companies to avoid. This is the system's memory, referenced by every downstream step.
Stage 2 ? Job discovery: A scheduled script (runs daily or hourly) hits job board APIs and RSS feeds ? LinkedIn, Indeed, Greenhouse, Lever, Workable all have programmatic access. You can also use a tool like Apify or Playwright to scrape boards that don't. Results land in a local database.
Stage 3 ? AI ranking: Claude scores each new listing against your profile, producing a match score and a short explanation. Low-score jobs get filtered out; high-score ones move forward. This is where you set your threshold for what "good enough to apply to" means.
Stage 4 ? Document generation: For each approved job, Claude reads the job description alongside your base resume and generates a tailored resume + cover letter, highlighting the most relevant experience and mirroring the employer's language.
Stage 5 ? Auto-apply: This is the hardest part. A browser automation tool (Playwright or Selenium) fills out application forms. Some job boards (LinkedIn Easy Apply, Greenhouse) are well-structured and reliable to automate. Others are inconsistent and may need manual fallback.
Stage 6 ? Tracking: Every application gets logged (company, role, date, score, status). This data feeds back into refining your criteria over time.

Where do you want to start? I can build you:
* An AI-powered artifact in Claude.ai to handle the ranking + document generation interactively right now
* A local Python script skeleton for the discovery + scheduling layer
* A full architecture plan with specific libraries and tools for each stage
????
?
You said: Try again
Try again
May 19
??
??
Claude responded: What would you like me to take another shot at?
What would you like me to take another shot at?
????
?
You said: Q: What would you like me to try again?
Q: What would you like me to try again? A: Your answer got interrupted. Resume it
May 19
??
??
Claude responded: Got it!
Got it! Picking up where I left off ? here's the complete breakdown of what to build and with what tools:

Recommended tech stack by stage
Stage 2 ? Job discovery
* feedparser for RSS feeds (many job boards publish them)
* LinkedIn Jobs API or linkedin-jobs-api npm package
* Playwright for scraping boards without APIs (Greenhouse, Lever, Workday)
* A simple SQLite database via sqlite3 or tinydb to deduplicate and store listings
Stage 3 ? AI ranking
* Claude API (claude-sonnet-4-20250514) with a structured prompt that takes your profile + job description and returns a JSON score + explanation
* Run this on every new listing that comes in from discovery
Stage 4 ? Document generation
* Claude API again ? one call per job to rewrite your resume bullets and generate a cover letter
* Store outputs as PDFs using weasyprint or reportlab
Stage 5 ? Auto-apply
* Playwright (Python or Node) is the best choice ? handles modern SPAs, has good waiting/retry logic
* Start with LinkedIn Easy Apply and Greenhouse, which are the most consistent
* Build a human-review gate first (approve before submitting) and automate submission only after you trust the pipeline
Stage 6 ? Tracking
* A simple SQLite table: company, role, url, score, applied_at, status, notes
* Optional: a lightweight local dashboard using Streamlit or a Claude.ai artifact

Recommended build order
1. Start with the Claude.ai artifact ? build an interactive job ranker and document generator you can use manually right now, zero infrastructure needed
2. Add the discovery script ? a cron job that pulls new listings into a local DB daily
3. Wire in the Claude API for automated scoring on new listings
4. Add Playwright for auto-apply last, once you trust everything upstream

What I can build for you right now:
* Interactive artifact ? paste a job description, get a match score against your profile + a tailored resume and cover letter generated instantly
* Python starter kit ? a full scaffolded project with discovery, scoring, and tracking modules ready to fill in
* Playwright apply script ? a template that handles LinkedIn Easy Apply form filling
Which would you like first?
????
?
You said: seems like you got interrupted again.
seems like you got interrupted again. Go ahead and resume
May 19
??
??
?2 / 2?
Claude responded: I actually wasn't interrupted ? that response finished completely.
I actually wasn't interrupted ? that response finished completely. The three options at the bottom were the natural ending, inviting you to pick what to build next.
Which would you like me to start on?
* Interactive artifact ? paste a job description, get a match score + tailored resume and cover letter right in Claude.ai
* Python starter kit ? scaffolded project with discovery, scoring, and tracking modules
* Playwright apply script ? template for automating LinkedIn Easy Apply form filling
????
?
You said: Well, you lost part of the converation.
Well, you lost part of the converation. I had told you I wanted to use multiple agents in case I was out of tokens time, and that I wanted this automated beyond updating my resume, setting salary, etc. so that I could stay on top of jobs without manual intervention
May 19
??
??
Claude responded: Thanks for clarifying!
Thanks for clarifying! I don't have that part of the conversation in my context ? it looks like it genuinely got cut off. Let me make sure I understand what you're after before building anything.
So the goal is a fully autonomous multi-agent system ? you set it up once (profile, resume, salary, preferences) and it runs continuously without you touching it. No manual review gates, no "paste this job description" steps. It finds jobs, ranks them, tailors your documents, applies, and tracks ? all on its own.
On the multi-agent side, can you recap what you had in mind?
This'll help me pick the right architecture before we start building.
????
?
You said: Q: How did you want the agents structured?
Q: How did you want the agents structured? A: Parallel agents running simultaneously across job boards
Q: How hands-off do you want it? A: Fully autonomous ? no approvals, just run
May 19
??
??
Claude responded: Perfect.
Perfect. Here's the architecture we're building toward:
V
visualize
V
visualize show_widget
Here's the full autonomous multi-agent architecture. Click any node for more detail. Here's how the whole system hangs together:
Orchestrator agent ? the brain. It runs on a cron schedule (every few hours), wakes up the crawler agents in parallel, deduplicates results as they come in, handles retries on failures, and pushes new unseen listings into the job database. Because each crawler is independent, a failure on one board doesn't stall the others.
Parallel crawler agents ? one per job board, running simultaneously. Each speaks the native language of its target: LinkedIn uses their API, Greenhouse/Lever use structured JSON endpoints, Indeed uses RSS + scraping, and you can add more boards by just adding another agent. Each one only ever reports new listings ? the orchestrator handles dedup.
Ranking agent ? Claude API call per listing. Takes the job description + your profile and returns a numeric fit score plus a short rationale. Listings below your threshold get marked "skip" and never touched again. This keeps the apply agent from wasting time on bad fits.
Doc generation agent ? for every listing that passes the threshold, this agent calls Claude to rewrite your resume bullets to match the job's language and generate a tailored cover letter. Outputs get stored alongside the listing.
Apply + track agent ? Playwright-based browser automation that submits the application and logs the result (timestamp, confirmation number if any, status). Everything is recorded so you can see your pipeline health over time.

Handling the token limit / interruption problem you mentioned ? the key is that each agent is stateless and checkpoints to the database. If any agent gets interrupted mid-run, the orchestrator just re-queues unfinished work on the next cycle. Nothing is lost because state lives in the DB, not in memory.

Ready to start building. Which piece do you want first ? the orchestrator + crawler scaffold, or the Claude-powered ranking and doc generation agents?
????
?
You said: Whichever you think is best.
Whichever you think is best. I?d like to be notified of jobs it applies for though. It can be completely automated, but I?d like to know
May 19
??
??
Claude responded: The crawler scaffold is the foundation everything else depends on, but honestly the most immediately useful thing to build first is the core Python project ? o?
The crawler scaffold is the foundation everything else depends on, but honestly the most immediately useful thing to build first is the core Python project ? orchestrator, crawlers, ranking, doc gen, apply, and notifications all wired together ? so you can run it end to end from day one, even if some boards start simple.
For notifications I'll wire in email alerts (via Gmail SMTP or SendGrid) so every time the apply agent submits an application, you get an email with the job title, company, link, your match score, and the cover letter it used. No app to install, works on any device.
Let me build the full scaffolded project now.
Ran 12 commands, created 4 files, read a file
Ran 12 commands, created 4 files, read a file
Here's the complete project. Here's exactly how to get it running:
1. Install dependencies
bash
cd job_agent
pip install -r requirements.txt
playwright install chromium
2. Edit config/profile.yaml ? the only file you need to touch:
* Paste your resume text
* Set your target titles, keywords, location, salary range
* Add your Anthropic API key
* Add your Gmail app password (generate one at myaccount.google.com/apppasswords) and your email address
* Add company slugs to greenhouse_companies (e.g. ["stripe", "linear", "notion"]) and lever_companies
3. Do a dry run first
bash
python orchestrator.py
dry_run: true is on by default ? it goes through the full pipeline (crawl, rank, generate docs) but doesn't actually click Submit. Check job_agent.log and the output/ folder to make sure the tailored resumes and cover letters look good.
4. Go live Set dry_run: false in the config, run again. From that point it runs every 4 hours (configurable) and emails you a digest every cycle it applies to anything ? one clean email listing every role, company, match score, and a direct link.
What happens if it gets interrupted: Every job that's crawled gets checkpointed to SQLite immediately. If the process dies mid-cycle, the next run picks up right where it left off ? already-applied jobs are skipped, already-scored jobs are skipped. Nothing gets double-applied.
Job agent
ZIPÿ
Download
????
?
?2 / 2?
You said: This is my resume.
2026 Jeff Bowers Resume.docx
5 lines
docx
This is my resume. Can you parse it into text that is easy to put into profile.yaml, and tell me some titles. I'd like my next job to either be senior sysadmin role, an IT director role, or system architect role. Those are my words to describe those jobs, but feel free to describe them better or add additional titles or keywords that will find the jobs. I'm located in Fort Smith, AR and don't want more then a 30 minute one-way commute, and I'm open to hybrid or remote. I'm even open to long distance hybrid depending on the distance and frequency.
May 20
??
??
Claude responded: Good news ? the document content was already extracted and visible in the conversation.
Check the resume content
Check the resume content
Good news ? the document content was already extracted and visible in the conversation. Here's everything you need for profile.yaml, fully formatted and ready to paste:

yaml
# ÄÄÄ Your profile ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄ
profile:
  name: "Jeffrey Bowers"
  email: "khantroll@gmail.com"
  phone: "479-322-0425"
  location: "Fort Smith, AR"
  linkedin: "https://linkedin.com/in/jeff-bowers-3a20069a/"
  github: ""

resume_text: |
  PROFESSIONAL SUMMARY
  Client-focused Systems Administrator with a focus on client and team relationships.
  Firmly believes the purpose of IT is to enable coworkers to do their jobs through
  supportive training, communication, and empathy. Experience as Level II Desktop Support
  Technician, IT Administrator for a vocational college system, and System Deployment Specialist.

  EXPERIENCE

  Senior Infrastructure / Systems Engineer ? City of Fort Smith, Fort Smith, AR (June 2023?Present)
  - Support and maintain infrastructure serving 800?1200 users across multiple municipal departments
  - Administer Active Directory and identity systems, including access control, group policy, and authentication
  - Manage VMware and Hyper-V virtualization environments for production systems
  - Contribute to disaster recovery planning and execution
  - Lead migration efforts from legacy systems to modern platforms, reducing technical debt
  - Implemented automated imaging and patch management solutions
  - Provide Tier III escalation support for complex infrastructure and application issues
  - Document systems and processes to improve operational consistency

  Systems Engineer (Endpoint & Automation Focus) ? Crafton Tull, Rogers, AR (Sept 2021?June 2023)
  - Supported 400+ endpoints across multiple sites, improving deployment efficiency 66% via imaging automation
  - Developed PowerShell automation scripts for user migrations, backups, and Active Directory cleanup
  - Managed asset lifecycle and endpoint provisioning using KACE
  - Delivered technical support for specialized engineering software environments

  Systems Administrator ? Vista College, Multi-State (May 2015?September 2021)
  - Managed infrastructure supporting ~2000 endpoints across 7 campuses and online division
  - Administered VPN infrastructure (IPSec, Cisco AnyConnect)
  - Implemented monitoring solutions using Elastic Stack and ManageEngine
  - Designed and improved onboarding processes, increasing operational efficiency
  - Delivered regular reporting and metrics to stakeholders

  Desktop Hardware Specialist ? Mercy Hospital, Fort Smith, AR (Feb 2014?Feb 2015)
  - Deployed systems across hospital and clinic environments as part of a 5-person team
  - Supported imaging and deployment via WDS and WSUS
  - Resolved high-volume ticket queues in a mission-critical healthcare environment

  TECHNICAL SKILLS
  CompTIA A+, Network+
  Linux (Ubuntu, Debian 7+, CentOS) and Windows Server Administration (WS2008R2?WS2016)
  Active Directory, Group Policy, Identity Management
  VMware, Hyper-V, VirtualBox, Proxmox
  Bash, PowerShell, JavaScript
  Docker Containerization
  MikroTik, Cisco Networking
  KACE, ManageEngine (Endpoint & Asset Management)
  Elasticsearch, Logstash, Kibana (ELK Stack)
  WDS, CloneZilla, FOG (Disk Imaging)
  Office 365 Administration
  Disaster Recovery Planning

  EDUCATION
  Master's Degree in IT Management ? WGU (Expected November 2026)
  Bachelor of Information Technology ? University of Arkansas Fort Smith (2006?2017)

  HONORS
  Vista College Employee of the Year for Customer Service ? 2020

# ÄÄÄ Job search preferences ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄ
search:
  titles:
    - "Senior Systems Administrator"
    - "Lead Systems Administrator"
    - "Infrastructure Manager"
    - "IT Director"
    - "Director of IT Operations"
    - "IT Operations Manager"
    - "Systems Architect"
    - "Infrastructure Architect"
    - "Solutions Architect"
    - "Senior Infrastructure Engineer"
    - "IT Manager"

  keywords:
    - "Active Directory"
    - "VMware"
    - "Hyper-V"
    - "Windows Server"
    - "PowerShell"
    - "infrastructure"
    - "disaster recovery"
    - "virtualization"
    - "endpoint management"
    - "Linux"

  location: "Fort Smith, AR"
  remote_ok: true
  hybrid_ok: true

  # For hybrid roles, the agent will flag anything within ~120 miles
  # for your review (covers NW Arkansas corridor, Oklahoma border region)
  max_commute_miles: 30

  salary_min: 80000
  salary_max: 150000        # Adjust ? Director roles in AR can hit $120?140k+
  job_types:
    - "full-time"

  exclude_companies: []
  exclude_keywords:
    - "blockchain"
    - "crypto"
    - "helpdesk only"
    - "entry level"

  min_match_score: 65

  # Add company slugs as you identify targets
  greenhouse_companies: []
  lever_companies: []

  rss_feeds:
    - "https://www.indeed.com/rss?q=systems+administrator&l=Fort+Smith+AR"
    - "https://www.indeed.com/rss?q=IT+director&l=Fort+Smith+AR"
    - "https://remoteok.com/remote-sysadmin-jobs.rss"
    - "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss"

A few notes on the title strategy:
"Senior Sysadmin" equivalents ? job boards use inconsistent language, so the list covers "Senior Systems Administrator," "Lead Systems Administrator," and "Senior Infrastructure Engineer" to catch the same role posted three different ways.
"IT Director" equivalents ? at your experience level (10+ years, managing infrastructure for 800?2000 users), you're competitive for Director and Manager titles, especially in Arkansas/regional markets. "IT Operations Manager" will surface roles that are director-level in scope but use different titles.
"Systems Architect" equivalents ? posted as "Infrastructure Architect" or "Solutions Architect" more often than "Systems Architect." With your virtualization, networking, and automation background, you have a real case for these, especially once your WGU master's lands.
On the commute/remote logic ? the current agent filters by location string and board-level remote flags, but doesn't calculate actual drive times. For the hybrid caveat you mentioned (open to longer distances depending on frequency), I'd suggest setting min_match_score a bit higher (say 75) for non-remote roles so only strong fits get through, and you can review those manually before the apply agent touches them. I can add a "flag for review" status to the pipeline for hybrid/on-site roles if you want.
????
?
You said: go ahead with those changes.
go ahead with those changes. Jobs are few in Fort Smith, but the drive time is important to me
May 20
??
??
Claude responded: Good call.
Good call. I'll add a commute-awareness layer ? hybrid/on-site jobs get flagged for your review instead of auto-applied, and the ranker will factor in drive time via Google Maps API before scoring.
Viewed 3 files
Viewed 3 files
Now I have a clear picture. Here's what I'm adding:
1. agents/commute.py ? Google Maps drive time check, classifies jobs as remote, auto_apply, or needs_review
2. data/db.py ? new commute_minutes, work_type, and status='needs_review' columns
3. agents/notifier.py ? separate review digest email with approve/skip instructions
4. orchestrator.py ? commute check wired into the pipeline before apply
5. config/profile.yaml ? commute and Maps API key fields
Ran 4 commands
Ran 4 commands
Now update the config with all the new fields and Jeff's actual profile:
Ran 3 commands, read a file
Ran 3 commands, read a file
Updated zip with everything wired in. Here's exactly how the commute logic works now:
Decision tree for every job that passes the score threshold:
Work typeCommuteActionRemote?Auto-apply ?On-siteó 30 minAuto-apply ?On-site31?90 minEmail you for review ??On-site> 90 minSkipped silentlyHybridAnyEmail you for review ??Hybrid always goes to review regardless of distance ? because the commute frequency matters as much as the drive time, and that's a judgment call only you can make.
Two separate emails per cycle:
* "? Applications submitted" ? the auto-applied jobs, same as before
* "?? Jobs needing your review" ? flagged jobs with commute time, work type badge, score, and a direct link. Docs are pre-generated and sitting in output/ so if you decide to apply to one, the resume and cover letter are already tailored and ready.
The only thing you need to add beyond what was already required is a Google Maps API key in config/profile.yaml. It's free up to $200/month of usage (roughly 40,000 drive-time lookups), and at your query volume you'll never come close to that. Without it the agent still works ? hybrid/on-site jobs just get flagged for review with "drive time unknown" instead of an exact number.

