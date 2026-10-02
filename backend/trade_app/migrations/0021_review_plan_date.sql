ALTER TABLE daily_reviews ADD COLUMN next_target_date TEXT;
CREATE INDEX daily_reviews_plan_target ON daily_reviews(account_id, next_target_date);
