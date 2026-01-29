"""
Tests for request validation schemas.

Tests Pydantic models for shuffle requests and query parameters.
"""

import pytest
from pydantic import ValidationError

from shuffify.schemas import (
    ShuffleRequest,
    PlaylistQueryParams,
    BasicShuffleParams,
    BalancedShuffleParams,
    StratifiedShuffleParams,
    PercentageShuffleParams,
    parse_shuffle_request,
)


class TestShuffleRequest:
    """Tests for ShuffleRequest schema."""

    def test_default_values(self):
        """Test that defaults are applied correctly."""
        request = ShuffleRequest()

        assert request.algorithm == 'BasicShuffle'
        assert request.keep_first == 0
        assert request.section_count == 4
        assert request.shuffle_percentage == 50.0
        assert request.shuffle_location == 'front'

    def test_valid_basic_shuffle(self):
        """Test valid BasicShuffle request."""
        request = ShuffleRequest(
            algorithm='BasicShuffle',
            keep_first=5
        )

        assert request.algorithm == 'BasicShuffle'
        assert request.keep_first == 5
        params = request.get_algorithm_params()
        assert params == {'keep_first': 5}

    def test_valid_balanced_shuffle(self):
        """Test valid BalancedShuffle request."""
        request = ShuffleRequest(
            algorithm='BalancedShuffle',
            keep_first=2,
            section_count=8
        )

        assert request.algorithm == 'BalancedShuffle'
        params = request.get_algorithm_params()
        assert params == {'keep_first': 2, 'section_count': 8}

    def test_valid_stratified_shuffle(self):
        """Test valid StratifiedShuffle request."""
        request = ShuffleRequest(
            algorithm='StratifiedShuffle',
            keep_first=1,
            section_count=10
        )

        assert request.algorithm == 'StratifiedShuffle'
        params = request.get_algorithm_params()
        assert params == {'keep_first': 1, 'section_count': 10}

    def test_valid_percentage_shuffle(self):
        """Test valid PercentageShuffle request."""
        request = ShuffleRequest(
            algorithm='PercentageShuffle',
            shuffle_percentage=75.5,
            shuffle_location='back'
        )

        assert request.algorithm == 'PercentageShuffle'
        params = request.get_algorithm_params()
        assert params == {'shuffle_percentage': 75.5, 'shuffle_location': 'back'}

    def test_invalid_algorithm_empty(self):
        """Test that empty algorithm raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            ShuffleRequest(algorithm='')

        assert 'Algorithm name cannot be empty' in str(exc_info.value)

    def test_invalid_algorithm_whitespace(self):
        """Test that whitespace-only algorithm raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            ShuffleRequest(algorithm='   ')

        assert 'Algorithm name cannot be empty' in str(exc_info.value)

    def test_invalid_algorithm_unknown(self):
        """Test that unknown algorithm raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            ShuffleRequest(algorithm='UnknownShuffle')

        assert 'Invalid algorithm' in str(exc_info.value)
        assert 'UnknownShuffle' in str(exc_info.value)

    def test_negative_keep_first(self):
        """Test that negative keep_first raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            ShuffleRequest(keep_first=-1)

        assert 'greater than or equal to 0' in str(exc_info.value).lower()

    def test_zero_section_count(self):
        """Test that zero section_count raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            ShuffleRequest(section_count=0)

        assert 'greater than or equal to 1' in str(exc_info.value).lower()

    def test_section_count_too_large(self):
        """Test that section_count over 100 raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            ShuffleRequest(section_count=101)

        assert 'less than or equal to 100' in str(exc_info.value).lower()

    def test_negative_shuffle_percentage(self):
        """Test that negative shuffle_percentage raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            ShuffleRequest(shuffle_percentage=-10.0)

        assert 'greater than or equal to 0' in str(exc_info.value).lower()

    def test_shuffle_percentage_over_100(self):
        """Test that shuffle_percentage over 100 raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            ShuffleRequest(shuffle_percentage=150.0)

        assert 'less than or equal to 100' in str(exc_info.value).lower()

    def test_invalid_shuffle_location(self):
        """Test that invalid shuffle_location raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            ShuffleRequest(shuffle_location='middle')

        # Pydantic should complain about invalid literal
        error_str = str(exc_info.value).lower()
        assert 'front' in error_str or 'back' in error_str

    def test_algorithm_name_stripped(self):
        """Test that algorithm name is stripped of whitespace."""
        request = ShuffleRequest(algorithm='  BasicShuffle  ')
        assert request.algorithm == 'BasicShuffle'

    def test_extra_fields_ignored(self):
        """Test that extra fields are ignored."""
        request = ShuffleRequest(
            algorithm='BasicShuffle',
            unknown_field='should be ignored'
        )
        assert request.algorithm == 'BasicShuffle'
        assert not hasattr(request, 'unknown_field')


class TestParseShuffleRequest:
    """Tests for parse_shuffle_request utility function."""

    def test_parse_string_form_data(self):
        """Test parsing string form data (as received from Flask)."""
        form_data = {
            'algorithm': 'BasicShuffle',
            'keep_first': '5'  # String from form
        }

        request = parse_shuffle_request(form_data)
        assert request.algorithm == 'BasicShuffle'
        assert request.keep_first == 5
        assert isinstance(request.keep_first, int)

    def test_parse_float_parameter(self):
        """Test parsing float parameter from string."""
        form_data = {
            'algorithm': 'PercentageShuffle',
            'shuffle_percentage': '75.5'
        }

        request = parse_shuffle_request(form_data)
        assert request.shuffle_percentage == 75.5
        assert isinstance(request.shuffle_percentage, float)

    def test_parse_with_defaults(self):
        """Test parsing with minimal data uses defaults."""
        form_data = {}

        request = parse_shuffle_request(form_data)
        assert request.algorithm == 'BasicShuffle'
        assert request.keep_first == 0

    def test_parse_invalid_integer(self):
        """Test parsing invalid integer raises ValidationError."""
        form_data = {
            'keep_first': 'not_a_number'
        }

        with pytest.raises(ValidationError):
            parse_shuffle_request(form_data)

    def test_parse_invalid_float(self):
        """Test parsing invalid float raises ValidationError."""
        form_data = {
            'shuffle_percentage': 'invalid'
        }

        with pytest.raises(ValidationError):
            parse_shuffle_request(form_data)


class TestPlaylistQueryParams:
    """Tests for PlaylistQueryParams schema."""

    def test_default_features_false(self):
        """Test that features defaults to False."""
        params = PlaylistQueryParams()
        assert params.features is False

    def test_features_true_string(self):
        """Test parsing 'true' string."""
        params = PlaylistQueryParams(features='true')
        assert params.features is True

    def test_features_false_string(self):
        """Test parsing 'false' string."""
        params = PlaylistQueryParams(features='false')
        assert params.features is False

    def test_features_yes_string(self):
        """Test parsing 'yes' string."""
        params = PlaylistQueryParams(features='yes')
        assert params.features is True

    def test_features_1_string(self):
        """Test parsing '1' string."""
        params = PlaylistQueryParams(features='1')
        assert params.features is True

    def test_features_0_string(self):
        """Test parsing '0' string."""
        params = PlaylistQueryParams(features='0')
        assert params.features is False

    def test_features_bool_true(self):
        """Test passing actual boolean True."""
        params = PlaylistQueryParams(features=True)
        assert params.features is True

    def test_features_bool_false(self):
        """Test passing actual boolean False."""
        params = PlaylistQueryParams(features=False)
        assert params.features is False

    def test_features_case_insensitive(self):
        """Test that boolean parsing is case insensitive."""
        assert PlaylistQueryParams(features='TRUE').features is True
        assert PlaylistQueryParams(features='True').features is True
        assert PlaylistQueryParams(features='YES').features is True
        assert PlaylistQueryParams(features='Y').features is True


class TestBasicShuffleParams:
    """Tests for BasicShuffleParams schema."""

    def test_default_keep_first(self):
        """Test default keep_first is 0."""
        params = BasicShuffleParams()
        assert params.keep_first == 0

    def test_valid_keep_first(self):
        """Test valid keep_first value."""
        params = BasicShuffleParams(keep_first=10)
        assert params.keep_first == 10

    def test_negative_keep_first(self):
        """Test negative keep_first raises error."""
        with pytest.raises(ValidationError):
            BasicShuffleParams(keep_first=-1)


class TestBalancedShuffleParams:
    """Tests for BalancedShuffleParams schema."""

    def test_defaults(self):
        """Test default values."""
        params = BalancedShuffleParams()
        assert params.keep_first == 0
        assert params.section_count == 4

    def test_valid_section_count(self):
        """Test valid section_count values."""
        params = BalancedShuffleParams(section_count=10)
        assert params.section_count == 10

    def test_boundary_section_count(self):
        """Test boundary values for section_count."""
        # Minimum
        params = BalancedShuffleParams(section_count=1)
        assert params.section_count == 1

        # Maximum
        params = BalancedShuffleParams(section_count=100)
        assert params.section_count == 100


class TestPercentageShuffleParams:
    """Tests for PercentageShuffleParams schema."""

    def test_defaults(self):
        """Test default values."""
        params = PercentageShuffleParams()
        assert params.shuffle_percentage == 50.0
        assert params.shuffle_location == 'front'

    def test_valid_percentage(self):
        """Test valid percentage values."""
        params = PercentageShuffleParams(shuffle_percentage=0.0)
        assert params.shuffle_percentage == 0.0

        params = PercentageShuffleParams(shuffle_percentage=100.0)
        assert params.shuffle_percentage == 100.0

    def test_valid_locations(self):
        """Test valid shuffle_location values."""
        params = PercentageShuffleParams(shuffle_location='front')
        assert params.shuffle_location == 'front'

        params = PercentageShuffleParams(shuffle_location='back')
        assert params.shuffle_location == 'back'
