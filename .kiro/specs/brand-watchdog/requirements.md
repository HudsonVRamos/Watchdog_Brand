# Requirements Document

## Introduction

Brand Watchdog is a monitoring system that detects unauthorized use of brand assets (logos and text mentions) on external websites. The system crawls a configured list of target websites, captures full-page screenshots (including below-the-fold content), analyzes them using a multimodal AI model via AWS Bedrock, and sends email alerts when unauthorized brand usage is detected.

## Glossary

- **Crawler**: The component responsible for navigating to target websites and capturing full-page screenshots using Playwright
- **Screenshot**: A full-page image capture of a target website, including all scrollable content (headers, body, footers)
- **Analyzer**: The component that sends screenshots to AWS Bedrock for AI-powered brand detection analysis
- **Bedrock_Agent**: The AWS Bedrock multimodal AI model configured to detect brand logos and text in screenshots
- **Alert_Service**: The component responsible for sending notification emails when unauthorized brand usage is detected
- **Brand_Asset**: A reference image of a brand logo or a brand text string provided by the user for detection purposes
- **Target_Site**: A URL configured for periodic monitoring by the system
- **Detection_Result**: The output from the Bedrock_Agent containing whether brand usage was found, the type of match (logo or text), confidence level, and location context
- **Monitoring_Schedule**: The configured frequency at which target sites are checked
- **Brand_Registry**: The collection of all Brand_Assets (reference logo images and brand text strings) that the system monitors for

## Requirements

### Requirement 1: Target Site Configuration

**User Story:** As a brand owner, I want to configure a list of websites to monitor, so that I can track where my brand may be used without authorization.

#### Acceptance Criteria

1. THE Brand Watchdog SHALL allow the user to register Target_Sites with a URL containing a scheme (http or https), a valid hostname, and an optional path, with a maximum URL length of 2048 characters
2. THE Brand Watchdog SHALL allow the user to remove Target_Sites from the monitoring list
3. THE Brand Watchdog SHALL validate that each Target_Site URL contains a scheme (http or https) and a syntactically valid hostname before accepting it
4. IF a duplicate Target_Site URL is submitted (compared after normalizing scheme and hostname to lowercase and removing trailing slashes), THEN THE Brand Watchdog SHALL reject the entry and return an error message indicating that the URL already exists in the monitoring list
5. IF a Target_Site URL that does not pass validation is submitted, THEN THE Brand Watchdog SHALL reject the entry and return an error message indicating the reason the URL is invalid
6. THE Brand Watchdog SHALL enforce a maximum of 200 Target_Sites per user account
7. IF the user attempts to register a Target_Site that would exceed the maximum limit of 200, THEN THE Brand Watchdog SHALL reject the entry and return an error message indicating the limit has been reached

### Requirement 2: Brand Asset Registration

**User Story:** As a brand owner, I want to register my brand logos and text identifiers, so that the system knows what to look for on monitored websites.

#### Acceptance Criteria

1. THE Brand Watchdog SHALL allow the user to register Brand_Assets as reference logo images in PNG, JPG, or SVG format with a maximum file size of 5 MB per image
2. THE Brand Watchdog SHALL allow the user to register Brand_Assets as brand text strings between 2 and 256 characters in length
3. THE Brand Watchdog SHALL store all Brand_Assets in the Brand_Registry for use during analysis
4. IF an unsupported image format is provided, THEN THE Brand Watchdog SHALL reject the upload and return an error message indicating that only PNG, JPG, and SVG formats are accepted
5. IF a Brand_Asset identical to an existing entry is submitted (same image file content or same text string), THEN THE Brand Watchdog SHALL reject the registration and return an error message indicating the asset already exists
6. IF a brand text string containing only whitespace or empty content is submitted, THEN THE Brand Watchdog SHALL reject the registration and return an error message indicating that the text string must contain at least 2 visible characters

### Requirement 3: Full-Page Web Crawling and Screenshot Capture

**User Story:** As a brand owner, I want the system to capture complete screenshots of target websites including content below the fold, so that brand usage in footers or hidden sections is not missed.

#### Acceptance Criteria

1. WHEN a monitoring cycle is triggered, THE Crawler SHALL navigate to each Target_Site and render the full page using Playwright with a viewport width of 1280 pixels
2. THE Crawler SHALL capture a full-page screenshot that includes all scrollable content from the top of the page to the bottom, including content loaded via lazy-loading by scrolling the entire page before capture
3. THE Crawler SHALL wait for the page to reach a network-idle state (no more than 2 active network connections for at least 500 milliseconds) before capturing the screenshot
4. IF the Crawler fails to load a Target_Site within 60 seconds, THEN THE Crawler SHALL log the failure with the Target_Site URL and timestamp, and proceed to the next Target_Site
5. IF a Target_Site returns an HTTP error status (4xx or 5xx), THEN THE Crawler SHALL log the error with the HTTP status code and Target_Site URL, skip the site, and proceed to the next Target_Site
6. IF the Crawler encounters a page with content exceeding 20,000 pixels in height after scrolling, THEN THE Crawler SHALL capture up to 20,000 pixels and log a warning indicating the screenshot was truncated

### Requirement 4: AI-Powered Brand Detection via AWS Bedrock

**User Story:** As a brand owner, I want the system to use AI to analyze screenshots for my brand logos and text, so that unauthorized usage is detected even when logos are resized, recolored, or distorted.

#### Acceptance Criteria

1. WHEN a screenshot is captured, THE Analyzer SHALL send the screenshot along with Brand_Registry reference assets to the Bedrock_Agent for analysis within 5 seconds of capture completion
2. WHEN the Bedrock_Agent receives a screenshot and reference assets, THE Bedrock_Agent SHALL detect brand logos in the screenshot even when they differ from reference images in color, size (scaled between 10% and 1000% of original), orientation (rotated up to 360°), or distortion (up to 30% geometric deformation from the original shape)
3. WHEN the Bedrock_Agent receives a screenshot and reference assets, THE Bedrock_Agent SHALL detect brand name text in the screenshot regardless of font, size (minimum 8px rendered height), or surrounding context
4. WHEN analysis is complete, THE Bedrock_Agent SHALL return a Detection_Result that includes: match type (logo or text), confidence level (integer 0-100), and bounding box coordinates (x, y, width, height as percentage of image dimensions) indicating where on the page the match was found
5. WHEN a Detection_Result has a confidence level equal to or above 60, THE Analyzer SHALL treat it as a confirmed match for further processing
6. IF the Bedrock_Agent is unavailable or returns an error, THEN THE Analyzer SHALL log the error and retry the analysis up to 3 times with exponential backoff starting at 2 seconds (2s, 4s, 8s)
7. IF all retry attempts fail, THEN THE Analyzer SHALL log the failure and mark the Target_Site analysis as incomplete for that monitoring cycle
8. IF the Bedrock_Agent does not return a Detection_Result within 60 seconds, THEN THE Analyzer SHALL treat the request as failed and proceed with the retry logic

### Requirement 5: Monitoring Schedule

**User Story:** As a brand owner, I want the system to automatically check target websites on a recurring schedule, so that I am notified of unauthorized brand usage in a timely manner.

#### Acceptance Criteria

1. THE Brand Watchdog SHALL execute monitoring cycles according to the configured Monitoring_Schedule
2. THE Brand Watchdog SHALL allow the user to configure the Monitoring_Schedule frequency with a minimum interval of 1 hour and a maximum interval of 720 hours (30 days), defaulting to 24 hours when no frequency is specified
3. WHEN a monitoring cycle starts, THE Brand Watchdog SHALL process all registered Target_Sites in that cycle by fetching each site's content and evaluating it against the configured brand detection rules
4. IF a monitoring cycle is still running when the next scheduled cycle is due, THEN THE Brand Watchdog SHALL skip the new cycle and log that it was skipped due to the previous cycle still being in progress
5. IF a Target_Site is unreachable during a monitoring cycle, THEN THE Brand Watchdog SHALL record the failure for that site, continue processing the remaining Target_Sites, and include the failure count in the cycle result summary
6. WHEN a monitoring cycle completes, THE Brand Watchdog SHALL log the start time, end time, number of sites processed successfully, number of sites that failed, and number of new brand violations detected

### Requirement 6: Alert Notifications

**User Story:** As a brand owner, I want to receive email alerts when unauthorized brand usage is detected, so that I can take action to protect my brand.

#### Acceptance Criteria

1. WHEN the Analyzer produces a Detection_Result with a confidence level above the configured threshold, THE Alert_Service SHALL send an individual alert email to each configured recipient for that Detection_Result
2. THE Alert_Service SHALL include in the alert email: the Target_Site URL, the type of brand match (logo or text), the confidence level (0-100), the description of the match location, and the timestamp of detection in ISO 8601 format
3. THE Alert_Service SHALL support sending emails via AWS SES or SMTP, as determined by the system configuration
4. IF the Alert_Service fails to send an email, THEN THE Alert_Service SHALL retry up to 3 times with a 30-second interval between attempts, and log the failure with the recipient address and Target_Site URL if all retries are exhausted
5. THE Brand Watchdog SHALL allow the user to configure the confidence threshold for triggering alerts as an integer value between 0 and 100 (default: 70)
6. THE Brand Watchdog SHALL allow the user to configure at least one email recipient address before alert notifications can be sent
7. IF the same Detection_Result (same Target_Site, same match type, and same match location) is found in consecutive monitoring cycles, THEN THE Alert_Service SHALL suppress duplicate alert emails and send a new alert only when the detection was absent in the previous cycle

### Requirement 7: Detection Result Storage

**User Story:** As a brand owner, I want detection results to be stored, so that I can review historical findings and track patterns of unauthorized brand usage.

#### Acceptance Criteria

1. WHEN a Detection_Result is produced, THE Brand Watchdog SHALL persist it with the associated Target_Site URL, timestamp, match type, and screenshot reference identifier within 5 seconds of production
2. IF persistence of a Detection_Result fails, THEN THE Brand Watchdog SHALL retry up to 3 times with exponential backoff and, if all retries fail, log the failure and notify the user that a result could not be stored
3. THE Brand Watchdog SHALL retain Detection_Results for a configurable retention period (minimum: 1 day, maximum: 365 days, default: 90 days)
4. WHEN the retention period for a Detection_Result expires, THE Brand Watchdog SHALL permanently delete the Detection_Result and its associated screenshot reference
5. THE Brand Watchdog SHALL allow the user to query stored Detection_Results by Target_Site, date range, or match type, returning results in reverse chronological order with a maximum of 100 results per query page
6. IF a query returns no matching Detection_Results, THEN THE Brand Watchdog SHALL return an empty result set with a message indicating no results were found for the specified filters

### Requirement 8: Screenshot Storage and Formatting

**User Story:** As a brand owner, I want captured screenshots to be stored for reference, so that I have evidence of unauthorized brand usage.

#### Acceptance Criteria

1. WHEN a screenshot is captured, THE Crawler SHALL store the screenshot as a valid PNG file and associate it with the Target_Site URL and a capture timestamp in UTC with second-level precision
2. IF the Crawler fails to store a screenshot, THEN THE Crawler SHALL retry up to 3 times and, if all retries fail, log the failure with the Target_Site URL and capture timestamp
3. THE Brand Watchdog SHALL retain stored screenshots for a configurable retention period (default: 90 days, minimum: 1 day, maximum: 365 days)
4. WHEN the retention period for a stored screenshot expires, THE Brand Watchdog SHALL automatically delete the screenshot and its associated metadata
5. THE Brand Watchdog SHALL store screenshots as PNG images parsed from raw Playwright output, ensuring that reading a stored screenshot and re-storing it produces a byte-for-byte identical file
