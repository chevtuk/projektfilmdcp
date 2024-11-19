from flask import Flask, request, render_template
import requests
from bs4 import BeautifulSoup

app = Flask(__name__)

SEARCH_URL = "https://www.svenskfilmdatabas.se/wp-admin/admin-ajax.php?action=quick_search&language=sv"

def search_movie(title, year=None):
    params = {"s": title}
    response = requests.get(SEARCH_URL, params=params)
    
    if response.status_code != 200:
        return None, None

    # Remove BOM if present
    response_text = response.text.lstrip("\ufeff")

    # Attempt to parse the response as JSON
    try:
        search_results = eval(response_text)
    except Exception:
        return None, None

    # Look for movies in the response
    for group in search_results:
        if "items" in group:
            for item in group["items"]:
                if "type=film" in item["url"]:
                    movie_title_with_year = item["title"]  # E.g., "Aftermath (2017)"
                    
                    # Extract year from the title
                    found_year = None
                    if "(" in movie_title_with_year and ")" in movie_title_with_year:
                        found_year = movie_title_with_year.split("(")[-1].split(")")[0]
                    
                    # Match title and optionally the year
                    if title.lower() in movie_title_with_year.lower():
                        if year:
                            if f"({year})" in movie_title_with_year:
                                movie_url = "https://www.svenskfilmdatabas.se" + item["url"]
                                return movie_url, found_year
                        else:
                            movie_url = "https://www.svenskfilmdatabas.se" + item["url"]
                            return movie_url, found_year

    return None, None

def check_dcp_availability(movie_url):
    response = requests.get(movie_url)
    if response.status_code != 200:
        return False

    # Parse the HTML content
    soup = BeautifulSoup(response.text, "html.parser")

    # Check all text within tables
    for table in soup.find_all("table"):
        if "DCP" in table.get_text(separator=" ", strip=True).upper():
            return True

    # Check all text within divs and paragraphs
    for section in soup.find_all(["div", "p"]):
        if "DCP" in section.get_text(separator=" ", strip=True).upper():
            return True

    # Finally, check the entire page as a fallback
    page_text = soup.get_text(separator=" ", strip=True)
    if "DCP" in page_text.upper():
        return True

    return False

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        movie_title = request.form.get('movie_title')
        release_year = request.form.get('release_year')  # Get the optional year input

        # Search movie and get both URL and year
        movie_url, found_year = search_movie(movie_title, year=release_year if release_year else None)

        if movie_url:
            dcp_available = check_dcp_availability(movie_url)
            year_display = found_year if found_year else "Unknown Year"
            if dcp_available:
                result = f"DCP is available for '{movie_title}' ({year_display})."
            else:
                result = f"DCP is not mentioned for '{movie_title}' ({year_display})."
        else:
            result = "Could not find the movie."

        return render_template('index.html', result=result)

    return render_template('index.html', result=None)

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))  # Use the PORT environment variable or default to 5000
    app.run(host="0.0.0.0", port=port, debug=False)

