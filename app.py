import os
import re
import datetime
from datetime import timedelta
import requests
import time
import random
from urllib.parse import urlparse
from io import BytesIO
from PIL import Image
import pickle
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import json
from flask import Flask, request, jsonify
import logging
import base64

# ----- CONFIG -----
SCOPES = ['https://www.googleapis.com/auth/blogger']
TOKEN_FILE = 'token_blog.pickle'
TEMP_IMAGE = 'company_logo.jpg'
INDIA_COUNTRY_FACET_ID = "c4f78be1a8f14da0ab49ce1162348a5e"  # Standard Workday facet ID for India
# ------------------

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)

# List of companies (focusing on Workday-based sites; extend for others)
COMPANIES = [
    {"name": "Boeing", "url": "https://boeing.wd1.myworkdayjobs.com/EXTERNAL_CAREERS"},
    {"name": "3M", "url": "https://3m.wd1.myworkdayjobs.com/search"},
    {"name": "Adobe", "url": "https://adobe.wd5.myworkdayjobs.com/external_experienced"},
    {"name": "NVIDIA", "url": "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"},
    {"name": "Salesforce", "url": "https://salesforce.wd12.myworkdayjobs.com/External_Career_Site"},
    {"name": "Target", "url": "https://target.wd5.myworkdayjobs.com/targetcareers"},
    {"name": "Walmart", "url": "https://walmart.wd5.myworkdayjobs.com/WalmartExternal"},
    {"name": "Chevron", "url": "https://chevron.wd5.myworkdayjobs.com/jobs"},
    {"name": "Deloitte", "url": "https://deloitteie.wd3.myworkdayjobs.com/Early_Careers"},
    {"name": "Puma", "url": "https://puma.wd3.myworkdayjobs.com/Jobs_at_Puma"},
    {"name": "Sanofi", "url": "https://sanofi.wd3.myworkdayjobs.com/SanofiCareers"},
    {"name": "Comcast", "url": "https://comcast.wd5.myworkdayjobs.com/Comcast_Careers"},
    {"name": "Abbott", "url": "https://abbott.wd5.myworkdayjobs.com/abbottcareers"},
    {"name": "Alcoa", "url": "https://alcoa.wd5.myworkdayjobs.com/careers/1/refreshFacet/318c8bb6f553100021d223d9780d30be"},
    {"name": "American Electric Power", "url": "https://aep.wd1.myworkdayjobs.com/AEPCareerSite"},
    {"name": "Amgen", "url": "https://amgen.wd1.myworkdayjobs.com/Careers"},
    {"name": "Applied Materials", "url": "https://amat.wd1.myworkdayjobs.com/External"},
    {"name": "Arrow Electronics", "url": "https://arrow.wd1.myworkdayjobs.com/AC"},
    {"name": "Assurant", "url": "https://assurant.wd1.myworkdayjobs.com/Assurant_Careers"},
    {"name": "AT&T", "url": "https://att.wd1.myworkdayjobs.com/ATTGeneral"},
    {"name": "Avis Budget Group", "url": "https://avisbudget.wd1.myworkdayjobs.com/ABG_Careers"},
    {"name": "BlackRock", "url": "https://blackrock.wd1.myworkdayjobs.com/BlackRock_Professional"},
    {"name": "Bupa", "url": "https://bupa.wd3.myworkdayjobs.com/EXT_CAREER"},
    {"name": "Cognizant", "url": "https://collaborative.wd1.myworkdayjobs.com/AllOpenings"},
]

def generate_auto_tags(title):
    """Generate tags from the job title."""
    stopwords = {'a', 'an', 'and', 'are', 'as', 'at', 'be', 'by', 'for', 'from', 'has', 'he', 'in', 'is', 'it', 'its', 'of', 'on', 'that', 'the', 'to', 'was', 'were', 'will', 'with'}
    words = re.sub(r'[^\w\s]', ' ', title.lower()).split()
    tags = [word for word in words if word not in stopwords and len(word) > 3][:5]
    return tags if tags else ['job', 'india', 'hiring']

def determine_exp_level(title, description):
    """Determine experience level from title and description."""
    text = (title + ' ' + description).lower()
    fresher_keywords = ['fresher', 'entry level', '0-1 year', 'junior', 'intern', 'graduate', 'fresh graduate']
    if any(keyword in text for keyword in fresher_keywords):
        return 'fresher'
    
    years_match = re.search(r'(\d+)[ -]?year[s]?', text)
    if years_match:
        years = years_match.group(1)
        return f"{years}exp"
    
    exp_keywords = ['senior', 'lead', 'manager', '2+', '3+']
    if any(keyword in text for keyword in exp_keywords):
        return 'exp'
    
    return 'fresher'  # Default

def get_company_logo(company_name):
    """Get company logo URL using Clearbit."""
    domain = company_name.lower().replace(' ', '') + '.com'
    logo_url = f"https://logo.clearbit.com/{domain}"
    try:
        r = requests.head(logo_url, timeout=10)
        if r.status_code == 200:
            return logo_url
    except requests.RequestException:
        pass
    logger.warning(f"Could not find logo for {company_name}.")
    return None

def fetch_past_jobs(company_name, base_url, target_date_str):
    """Fetch jobs posted on target date from Workday site using API endpoint."""
    parsed_url = urlparse(base_url)
    host = parsed_url.netloc
    path = parsed_url.path.strip('/')
    if 'en-US/' in path:
        site = path.split('en-US/')[1]
    else:
        site = path
    tenant = host.split('.')[0]
    endpoint = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive',
        'Referer': base_url,
    }

    jobs = []
    offset = 0
    limit = 20
    today = datetime.date.today()
    has_more = True
    while has_more:
        data = {
            "appliedFacets": {"locationCountry": [INDIA_COUNTRY_FACET_ID]},
            "limit": limit,
            "offset": offset,
            "searchText": ""
        }
        try:
            r = requests.post(endpoint, headers=headers, json=data, timeout=10)
            if r.status_code != 200:
                logger.error(f"Failed to fetch jobs for {company_name}: {r.status_code} - {r.reason}")
                return jobs
            
            try:
                response_data = r.json()
            except ValueError:
                logger.error(f"Response not JSON for {company_name}")
                return jobs

            job_postings = response_data.get('jobPostings', [])
            if not job_postings:
                has_more = False
                break

            for posting in job_postings:
                title = posting.get('title', 'Unknown Title')
                external_path = posting.get('externalPath', '')
                if not external_path:
                    continue
                slug = external_path.split('/')[-1]
                try:
                    title_slug, job_req_id = slug.rsplit('_', 1)
                except ValueError:
                    title_slug = slug
                    job_req_id = slug
                apply_link = f"https://{host}/en-US/{site}/details/{slug}?q={job_req_id}"
                location = posting.get('locationsText', '')
                if 'india' not in location.lower():
                    continue
                
                posted_text = posting.get('postedOn', '')
                posted_delta = 0
                if 'Today' in posted_text or 'today' in posted_text.lower():
                    posted_delta = 0
                elif re.search(r'(\d+) day[s]? ago', posted_text, re.I):
                    match = re.search(r'(\d+) day[s]? ago', posted_text, re.I)
                    if match:
                        posted_delta = int(match.group(1))
                elif re.search(r'posted (\d+) days? ago', posted_text, re.I):
                    match = re.search(r'posted (\d+) days? ago', posted_text, re.I)
                    if match:
                        posted_delta = int(match.group(1))
                
                posted_date = (today - timedelta(days=posted_delta)).isoformat()
                
                date_match = re.search(r'\d{4}-\d{2}-\d{2}', posted_text)
                if date_match:
                    posted_date = date_match.group(0)
                
                if posted_date != target_date_str:
                    if posted_delta > (today - datetime.date.fromisoformat(target_date_str)).days:
                        has_more = False
                    continue
                
                # Fetch description from detail endpoint
                detail_endpoint = f"https://{host}/wday/cxs/{tenant}/{site}/job{external_path}"
                try:
                    detail_r = requests.get(detail_endpoint, headers=headers, timeout=10)
                    if detail_r.status_code == 200:
                        detail_data = detail_r.json()
                        original_desc = detail_data.get('jobPostingInfo', {}).get('jobDescription', title + ' - ' + location + '. Exciting opportunity at ' + company_name + ' in India.')
                        description = re.sub(r'<[^>]+>', '', original_desc)  # Strip HTML for extraction
                        skills = re.findall(r'\b(?:[A-Za-z0-9+.#]+(?:/[A-Za-z0-9+.#]+)?|[A-Za-z]+)\b(?=\s*(?:,|\.|;|\sand\s|\sor\s|\(|\)))', description, re.I)
                        skills = list(set([skill for skill in skills if len(skill) > 2 and not re.match(r'^\d+$', skill)]))[:5]
                        experience_match = re.search(r'(\d+\s*-\s*\d+\s*(?:year[s]?)?(?:\s*of\s*experience)?|\d+\s*\+\s*(?:year[s]?)?(?:\s*of\s*experience)?)', description, re.I)
                        experience = experience_match.group(0) if experience_match else "Not specified"
                    else:
                        logger.warning(f"Failed to fetch details for {title}: {detail_r.status_code}")
                        original_desc = title + ' - ' + location + '. Exciting opportunity at ' + company_name + ' in India.'
                        description = original_desc
                        skills = ['Not specified']
                        experience = 'Not specified'
                except Exception as e:
                    logger.warning(f"Could not fetch details for {title}: {str(e)}")
                    original_desc = title + ' - ' + location + '. Exciting opportunity at ' + company_name + ' in India.'
                    description = original_desc
                    skills = ['Not specified']
                    experience = 'Not specified'
                
                exp_level = determine_exp_level(title, description)
                
                jobs.append({
                    'title': title,
                    'description': original_desc,
                    'apply_link': apply_link,
                    'posted_date': posted_date,
                    'exp': exp_level,
                    'company': company_name,
                    'location': location,
                    'skills': skills,
                    'experience': experience
                })
            
            offset += limit
            total = response_data.get('total', 0)
            if offset >= total:
                has_more = False
        
        except Exception as e:
            logger.error(f"Error fetching jobs for {company_name}: {str(e)}")
            has_more = False
    
    return jobs

def generate_post_title(job):
    """Generate post title based on experience level."""
    base_title = re.sub(r'\s+-\s+.*', '', job['title']).strip()  # Clean base title
    exp_str = job['exp']
    if exp_str == 'fresher':
        return f"{base_title} - fresher"
    else:
        return f"{base_title} - {exp_str}"

def simple_article_from_job(job, logo_url=None):
    """Generate HTML content for blog post from job details."""
    post_title = generate_post_title(job)
    content_html = f"<h2>{post_title}</h2>\n"
    if logo_url:
        content_html += f"<img src='{logo_url}' alt='{job['company']} logo' style='max-width:100px;height:auto;margin-bottom:20px;'>\n"
    content_html += f"<h3>{job['company']} - {job['posted_date']}</h3>\n"
    content_html += f"{job['description']}\n"
    content_html += f"<p><strong>Location:</strong> {job['location']}</p>\n"
    content_html += f"<p><strong>Skills:</strong> {', '.join(job['skills'])}</p>\n"
    content_html += f"<p><strong>Experience:</strong> {job['experience']}</p>\n"
    content_html += f"<p><strong>Apply Now:</strong> <a href='{job['apply_link']}'>Apply Link</a></p>\n"
    return content_html

def authenticate_blogger(creds_json=None):
    """Authenticate with Blogger API using OAuth."""
    creds = None
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, 'rb') as tk:
            creds = pickle.load(tk)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if creds_json:
                with open('temp_credentials.json', 'w') as f:
                    json.dump(creds_json, f)
                flow = InstalledAppFlow.from_client_secrets_file('temp_credentials.json', SCOPES)
                creds = flow.run_local_server(port=0)
                os.remove('temp_credentials.json')
            else:
                creds_json = json.loads(os.environ.get('CREDENTIALS_JSON', '{}'))
                with open('temp_credentials.json', 'w') as f:
                    json.dump(creds_json, f)
                flow = InstalledAppFlow.from_client_secrets_file('temp_credentials.json', SCOPES)
                creds = flow.run_local_server(port=0)
                os.remove('temp_credentials.json')
        with open(TOKEN_FILE, 'wb') as tk:
            pickle.dump(creds, tk)
    service = build('blogger', 'v3', credentials=creds)
    return service

def create_post(service, blog_id, title, content_html, labels=None, max_retries=3, retry_delay=60):
    """Create a new post on Blogger with retry mechanism."""
    for attempt in range(max_retries):
        try:
            body = {"kind": "blogger#post", "blog": {"id": blog_id}, "title": title, "content": content_html}
            if labels:
                body['labels'] = labels
            post = service.posts().insert(blogId=blog_id, body=body, isDraft=False).execute()
            return post
        except HttpError as e:
            if 'quotaExceeded' in str(e):
                logger.warning(f"Quota exceeded for {title}. Retrying after {retry_delay} seconds ({attempt+1}/{max_retries})...")
                time.sleep(retry_delay)
            else:
                raise e
    logger.error(f"Failed to post {title} after {max_retries} attempts due to quota limit.")
    return None

def validate_blog_id(blog_id):
    """Validate that the blog ID is a single numeric value."""
    if not re.match(r'^\d+$', blog_id):
        raise ValueError("Invalid blog ID. It should be a single numeric value (e.g., 7594720483112523181). Check your Blogger dashboard under Settings > Basic > Blog ID.")

@app.route('/api/post-jobs', methods=['POST'])
def post_jobs():
    try:
        data = request.get_json()
        blog_id = data.get('blog_id')
        days_ago = data.get('days_ago', 0)
        credentials = data.get('credentials')

        validate_blog_id(blog_id)
        target_date = (datetime.date.today() - timedelta(days=days_ago)).isoformat()

        logger.info("Authenticating to Blogger...")
        service = authenticate_blogger(credentials)

        logger.info(f"Fetching jobs posted {days_ago} days ago ({target_date})...")

        random.shuffle(COMPANIES)

        posted_count = 0
        for company in COMPANIES:
            logger.info(f"Processing {company['name']}...")
            jobs = fetch_past_jobs(company['name'], company['url'], target_date)
            for job in jobs:
                logo_url = get_company_logo(company['name'])
                html_content = simple_article_from_job(job, logo_url)
                post_title = generate_post_title(job)
                labels = generate_auto_tags(post_title)
                logger.info(f"Creating post for: {post_title}")
                try:
                    post = create_post(service, blog_id, post_title, html_content, labels=labels)
                    if post:
                        logger.info(f"Posted! Post URL: {post.get('url')}")
                        posted_count += 1
                    delay = random.uniform(30, 60)
                    logger.info(f"Waiting {delay:.2f} seconds before next post...")
                    time.sleep(delay)
                except Exception as e:
                    logger.error(f"Failed to post {post_title}: {str(e)}")

        return jsonify({"status": "success", "posted_count": posted_count}), 200
    except Exception as e:
        logger.error(f"Error in post_jobs: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))