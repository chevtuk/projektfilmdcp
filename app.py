from flask import Flask, request, render_template
import requests
from bs4 import BeautifulSoup
import unicodedata
import re
from fuzzywuzzy import fuzz
import logging

logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)

SEARCH_URL = "https://www.svenskfilmdatabas.se/wp-admin/admin-ajax.php?action=quick_search&language=sv"

def normalize_title(title):
    """Normalize titles to lowercase and remove accents/special characters."""
    # Remove accents and special characters
    title = unicodedata.normalize('NFKD', title).encode('ascii', 'ignore').decode('utf-8')
    # Remove non-alphanumeric characters (except spaces)
    title = re.sub(r'[^a-zA-Z0-9\s]', '', title)
    return title.lower().strip()

def search_movie(title, year=None):
    params = {"s": title}
    response = requests.get(SEARCH_URL, params=params)
    
    if response.status_code != 200:
        logging.error("Failed to fetch search results.")
        return None, None

    # Remove BOM if present
    response_text = response.text.lstrip("\ufeff")

    # Log raw response
    logging.debug(f"Search response: {response_text}")

    # Attempt to parse the response as JSON
    try:
        search_results = eval(response_text)
    except Exception as e:
        logging.error(f"Error parsing search results: {e}")
        return None, None

    # Normalize input title
    normalized_input_title = normalize_title(title)
    logging.debug(f"Normalized input title: {normalized_input_title}")

    best_match = None
    best_year = None
    best_score = 0

    # Look for movies in the response
    for group in search_results:
        if "items" in group:
            for item in group["items"]:
                if "type=film" in item["url"]:
                    movie_title_with_year = item["title"]  # E.g., "Sagan om de två tornen - härskarringen (2002)"
                    
                    # Normalize movie title
                    normalized_movie_title = normalize_title(movie_title_with_year)
                    logging.debug(f"Checking movie title: {movie_title_with_year} (Normalized: {normalized_movie_title})")

                    # Exclude trailers and other unwanted results
                    if "trailer" in normalized_movie_title:
                        continue

                    # Calculate similarity score
                    score = fuzz.ratio(normalized_input_title, normalized_movie_title)
                    logging.debug(f"Similarity score: {score} for {movie_title_with_year}")

                    if score > best_score and score > 70:  # Only consider matches above 70% similarity
                        best_score = score
                        found_year = None
                        if "(" in movie_title_with_year and ")" in movie_title_with_year:
                            found_year = movie_title_with_year.split("(")[-1].split(")")[0]
                        best_match = "https://www.svenskfilmdatabas.se" + item["url"]
                        best_year = found_year

    if best_match:
        logging.debug(f"Best match: {best_match}")
    else:
        logging.warning("No matching movie found.")

    return best_match, best_year

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
            movie_url = None

        return render_template('index.html', result=result, movie_url=movie_url)

    return render_template('index.html', result=None, movie_url=None)

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))  # Use the PORT environment variable or default to 5000
    app.run(host="0.0.0.0", port=port, debug=False)
