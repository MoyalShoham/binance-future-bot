"""
Sentiment Analyzer Agent Implementation

Synthesizes market sentiment from news, social media, and order book dynamics.
Uses Haiku 4.5 for fast sentiment classification.
"""

from typing import Dict, Any, Optional
from datetime import datetime
import uuid
import structlog
import json

from .base_agent import BaseAgent

logger = structlog.get_logger()


class SentimentAnalyzerAgent(BaseAgent):
    """
    Sentiment Analyzer Agent
    
    Responsibilities:
    - Aggregate news events from CryptoPanic
    - Analyze social sentiment (Twitter, Reddit)
    - Analyze order book imbalance (buy/sell pressure)
    - Synthesize into composite sentiment score
    - Flag extreme readings and conflicts
    
    Model: Claude Haiku 4.5 (fast classification)
    Authority: Sentiment opinion only (confluence check)
    """
    
    def __init__(
        self,
        agent_id: str,
        config: Dict[str, Any],
        model_router=None
    ):
        """
        Initialize Sentiment Analyzer Agent.
        
        Args:
            agent_id: Agent identifier
            config: System configuration
            model_router: Optional ModelRouter for LLM calls
        """
        super().__init__(agent_id, config, model_router=model_router)
        
        self.config = config
        self.min_sample_size = config.get("agents", {}).get("sentiment", {}).get("min_sample_size", 1000)
        self.news_recency = config.get("agents", {}).get("sentiment", {}).get("news_recency_min", 30)
        self.extreme_threshold = config.get("agents", {}).get("sentiment", {}).get("extreme_threshold", 0.85)
    
    async def analyze(self, sentiment_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analyze sentiment from multiple sources.
        
        Args:
            sentiment_data: News, social, order book data
            
        Returns:
            Composite sentiment analysis with drivers
        """
        request_id = str(uuid.uuid4())
        self.logger.info(
            "sentiment_analysis_start",
            request_id=request_id,
            data_sources=list(sentiment_data.keys())
        )
        
        try:
            # If LLM enabled, use synthesis
            if self.model_router and self.llm_enabled:
                from prompts.sentiment_analyzer import SENTIMENT_SYSTEM
                
                analysis = await self.model_router.call_model(
                    system_prompt=SENTIMENT_SYSTEM,
                    user_message=self._format_sentiment_prompt(sentiment_data),
                    model="haiku-4-5",  # Use Haiku for speed
                    response_format="json"
                )
                
                sentiment = json.loads(analysis) if isinstance(analysis, str) else analysis
            else:
                # Rule-based analysis
                sentiment = self._analyze_rule_based(sentiment_data)
            
            # Validate output
            if self.validator.has_schema("sentiment_output"):
                sentiment = self.validator.validate(sentiment, "sentiment_output")
            
            self.logger.info(
                "sentiment_analysis_complete",
                request_id=request_id,
                sentiment_score=sentiment.get("composite_sentiment"),
                confidence=sentiment.get("confidence")
            )
            
            return {
                "status": "success",
                "sentiment": sentiment,
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }
            
        except Exception as e:
            self.logger.error(
                "sentiment_analysis_error",
                request_id=request_id,
                error=str(e)
            )
            return {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }
    
    def _format_sentiment_prompt(self, sentiment_data: Dict[str, Any]) -> str:
        """Format sentiment data into analysis prompt."""
        return f"""
Analyze the following market sentiment data and provide a composite sentiment score:

News Events:
{json.dumps(sentiment_data.get("news_events", []), indent=2)}

Social Sentiment:
{json.dumps(sentiment_data.get("social_sentiment", {}), indent=2)}

Order Book:
{json.dumps(sentiment_data.get("order_book", {}), indent=2)}

Fear Greed Index: {sentiment_data.get("fear_greed_index")}

Provide composite sentiment score (-1.0 to 1.0), confidence (0-1.0), key drivers, and any conflicting signals.
Return as JSON.
"""
    
    def _analyze_rule_based(self, sentiment_data: Dict[str, Any]) -> Dict[str, Any]:
        """Rule-based sentiment analysis without LLM."""
        
        # News sentiment
        news_events = sentiment_data.get("news_events", [])
        news_score = 0.0
        positive_news = sum(1 for e in news_events if e.get("sentiment") == "positive")
        negative_news = sum(1 for e in news_events if e.get("sentiment") == "negative")
        
        if len(news_events) > 0:
            news_score = (positive_news - negative_news) / len(news_events)
        
        # Social sentiment
        social = sentiment_data.get("social_sentiment", {})
        twitter_score = social.get("twitter_trend", {}).get("score", 0.5)
        reddit_score = social.get("reddit_sentiment", {}).get("score", 0.5)
        
        # Order book imbalance
        ob = sentiment_data.get("order_book", {})
        buy_ratio = ob.get("buy_wall_ratio", 1.0)
        ob_score = (buy_ratio - 1.0) / 2.0 if buy_ratio > 0 else 0  # Normalize 0.5-1.5 to -0.25 to 0.25
        
        # Fear/greed
        fg = sentiment_data.get("fear_greed_index", 50)
        fg_score = (fg - 50) / 100  # Normalize 0-100 to -0.5 to 0.5
        
        # Composite (weighted average)
        composite = (
            0.4 * news_score +
            0.3 * ((twitter_score + reddit_score) / 2 - 0.5) +
            0.2 * ob_score +
            0.1 * fg_score
        )
        composite = max(-1.0, min(1.0, composite))
        
        # Confidence
        confidence = 0.7 if len(news_events) > 3 else 0.5
        
        return {
            "composite_sentiment": composite,
            "confidence": confidence,
            "sentiment_label": "bullish" if composite > 0.2 else "bearish" if composite < -0.2 else "neutral",
            "drivers": [
                f"News: {positive_news}/{len(news_events)} positive" if news_events else None,
                f"Social: {social.get('community_index', 0.5):.0%} positive",
                f"Order book: {buy_ratio:.2f}x buy pressure"
            ],
            "conflicting_signals": [],
            "extreme_readings": abs(composite) > self.extreme_threshold,
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }

