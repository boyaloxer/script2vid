# script2vid - Action Plan

## Footage Source

### Pexels Video API

Pexels offers a massive library of over 100,000 high-quality, royalty-free stock videos (HD to 4K) contributed by creators worldwide. It's one of the largest free sources, with diverse categories like nature, people, technology, and abstract clips.

- **API Access:** Free with sign-up for an API key (via their site). Use it in the `Authorization` header for requests.

- **Searching:**
  - `GET https://api.pexels.com/videos/search?query=your_script_keywords`
  - Parameters: `query` (keywords from script), `size` (e.g., `large` for 4K), `orientation` (landscape/portrait), `page`, `per_page` (up to 80 results).
  - `GET https://api.pexels.com/videos/popular` for trending clips filtered by duration or dimensions.

- **Analysis by Your AI:**
  - Responses include JSON with video metadata: tags, duration, width/height, user info, and preview images.
  - Your AI can parse this to score relevance (e.g., match script descriptions to tags) before selecting.

- **Downloading:**
  - Direct MP4 URLs in the response (under `video_files` array, with qualities like HD/SD/HLS).
  - Programmatically fetch them — no extra steps needed.

- **Limitations:**
  - Rate limit of 200 requests/hour and 20,000/month (can request increases for free with attribution proof).
  - No bulk downloading abuse allowed; cache results where possible.

- **How to Integrate:**
  - Your AI could use libraries like Python's `requests` to query, then analyze JSON outputs.
  - Attribution to Pexels and creators is encouraged but not always required.

- **Get Started:**
  - Docs: https://www.pexels.com/api/documentation
  - Sign up for key: https://pexels.com
