ALTER TABLE daily_reviews ADD COLUMN overall_summary TEXT NOT NULL DEFAULT '';
ALTER TABLE daily_reviews ADD COLUMN reflection TEXT NOT NULL DEFAULT '';
ALTER TABLE daily_reviews ADD COLUMN tags_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE daily_reviews ADD COLUMN next_market_forecast TEXT NOT NULL DEFAULT '';
ALTER TABLE daily_reviews ADD COLUMN next_watchlist_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE daily_reviews ADD COLUMN next_position_plan TEXT NOT NULL DEFAULT '';
ALTER TABLE daily_reviews ADD COLUMN next_risk_plan TEXT NOT NULL DEFAULT '';
ALTER TABLE daily_reviews ADD COLUMN next_position_rehearsal_json TEXT NOT NULL DEFAULT '[]';
